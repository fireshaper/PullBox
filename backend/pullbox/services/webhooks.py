"""Outbound webhooks: event registry, payload shaping and delivery.

Call sites do two things, in this order:

1. ``build_issue_payload(...)`` **inside** the DB session, while the ORM rows
   are loaded, to snapshot everything the notification needs into plain dicts.
2. ``emit(event, payload)`` **after** ``db.commit()``. It only schedules a task
   and returns immediately, so nothing here ever runs inside a caller's
   transaction — a delivery is a network call with retries and back-off, and
   holding a SQLite write lock across it would stall the APScheduler datastore
   (see ``services/queue.process_job`` for how that fails).

The task opens its own short read session to find subscribed hooks, closes it,
delivers to all of them concurrently, then records each outcome in a second
short session. Delivery failures never propagate: a dead endpoint costs a log
line and a red status in Settings → Webhooks, nothing more.

Two payload ``format``s:

* ``generic`` — PullBox's own JSON envelope (``{"event", "timestamp", "source",
  ...data}``) for companion apps such as Thwip. When the hook has a ``secret``
  the body is signed with HMAC-SHA256 in ``X-PullBox-Signature``.
* ``discord`` — a Discord webhook payload with one embed per event.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import httpx

from pullbox.services.general import relative_to_library

logger = logging.getLogger("pullbox")

# ── Event registry ───────────────────────────────────────────────────────────
#
# Names are dotted ``subject.verb``. Adding one: register it here (the API and
# the Settings UI both read this table), give it a Discord rendering in
# ``_discord_body`` and emit it from the call site after commit.

EVENTS: dict[str, str] = {
    "download.started": "A release was sent to the download client",
    "download.completed": "An issue finished downloading (and post-processing, if enabled)",
    "download.failed": "The download client reported a failure; the issue will be retried",
    "download.exhausted": "An issue hit the retry limit and was given up on",
    "series.added": "A series was added to the library",
    "test": "Sent by the Test button in Settings → Webhooks",
}

FORMATS: tuple[str, ...] = ("generic", "discord")

USER_AGENT = "PullBox/0.1.0 (+https://github.com/fireshaper/pullbox)"

# One delivery: a short timeout and a couple of quick retries. Anything longer
# would let a hung endpoint pin a task for minutes; the user sees the failure
# in the UI and can hit Test once it is back.
TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 3
RETRY_DELAYS = (1.0, 3.0)
# How long a 429's Retry-After is honoured before giving up on the attempt.
MAX_RETRY_AFTER = 10.0


@dataclass(frozen=True)
class WebhookTarget:
    """A detached snapshot of one ``Webhook`` row — safe to use after the session closes."""

    id: int | None
    name: str
    url: str
    format: str
    secret: str | None


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    message: str
    status_code: int | None = None


# ── Payload builders ─────────────────────────────────────────────────────────


def _iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def build_issue_payload(
    issue,
    series,
    library_path: str | None,
    *,
    job=None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Snapshot an issue (+ its series and optional job) into a JSON-ready dict.

    Must be called while ``issue``/``series``/``job`` are attached to a live
    session. ``path_rel`` mirrors the companion feed: the file path below the
    library root, which is the only portable join key when the receiver sees
    the library under a different mount point.
    """
    file_path = issue.file_path or None
    payload: dict[str, Any] = {
        "issue": {
            "id": issue.id,
            "series_id": issue.series_id,
            "metron_id": issue.metron_id,
            "comicvine_id": issue.comicvine_id,
            "issue_number": issue.issue_number,
            "title": issue.title,
            "cover_date": _iso(issue.cover_date),
            "store_date": _iso(issue.store_date),
            "cover_url": issue.cover_url,
            "status": issue.status,
            "file_path": file_path,
            "path_rel": (
                relative_to_library(file_path, library_path) if file_path and library_path else None
            ),
        },
        "series": build_series_payload(series),
        "job": None,
    }
    if job is not None:
        payload["job"] = {
            "id": job.id,
            "status": job.status,
            "attempts": job.attempts,
            "result_title": job.result_title,
            "source_type": job.source_type,
            "download_client_type": job.download_client_type,
            "next_attempt_at": _iso(job.next_attempt_at),
        }
    if reason is not None:
        payload["reason"] = reason
    return payload


def build_series_payload(series) -> dict[str, Any]:
    return {
        "id": series.id,
        "metron_id": series.metron_id,
        "comicvine_id": series.comicvine_id,
        "title": series.title,
        "publisher": series.publisher,
        "start_year": series.start_year,
        "subscribed": series.subscribed,
        "auto_download": series.auto_download,
        "cover_url": series.cover_url,
    }


def build_test_payload(name: str) -> dict[str, Any]:
    """Body for the Settings → Test button. Deliberately carries no issue data so
    a receiver acting on ``download.completed`` never tries to import a file
    that does not exist — consumers key on ``event``."""
    return {"message": f"Test delivery from PullBox to webhook {name!r}"}


# ── Body shaping ─────────────────────────────────────────────────────────────


def build_body(fmt: str, event: str, payload: dict[str, Any], timestamp: datetime) -> dict:
    """Shape the outgoing JSON for ``fmt``."""
    if fmt == "discord":
        return _discord_body(event, payload, timestamp)
    return {"event": event, "timestamp": _iso(timestamp), "source": "pullbox", **payload}


def sign(secret: str, body: bytes) -> str:
    """``sha256=<hex>`` HMAC of the exact bytes on the wire."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# Discord embed colours (decimal RGB).
_COLOR_GREEN = 0x3BA55D
_COLOR_BLUE = 0x5865F2
_COLOR_ORANGE = 0xE67E22
_COLOR_RED = 0xED4245
_COLOR_PURPLE = 0x9B59B6
_COLOR_GREY = 0x95A5A6


def _issue_label(payload: dict[str, Any]) -> str:
    issue = payload.get("issue") or {}
    series = payload.get("series") or {}
    title = series.get("title") or "Unknown series"
    number = issue.get("issue_number")
    return f"{title} #{number}" if number else title


def _field(name: str, value: Any, inline: bool = True) -> dict | None:
    if value in (None, ""):
        return None
    return {"name": name, "value": str(value)[:1024], "inline": inline}


def _discord_body(event: str, payload: dict[str, Any], timestamp: datetime) -> dict:
    issue = payload.get("issue") or {}
    series = payload.get("series") or {}
    job = payload.get("job") or {}
    label = _issue_label(payload)

    fields: list[dict | None] = []
    thumbnail = issue.get("cover_url") or series.get("cover_url")

    if event == "download.completed":
        title, color = f"Downloaded: {label}", _COLOR_GREEN
        fields = [
            _field("Issue title", issue.get("title")),
            _field("Publisher", series.get("publisher")),
            _field("File", issue.get("path_rel") or issue.get("file_path"), inline=False),
        ]
    elif event == "download.started":
        title, color = f"Downloading: {label}", _COLOR_BLUE
        fields = [
            _field("Release", job.get("result_title"), inline=False),
            _field("Client", job.get("download_client_type")),
            _field("Attempt", job.get("attempts")),
        ]
    elif event == "download.failed":
        title, color = f"Download failed: {label}", _COLOR_ORANGE
        fields = [
            _field("Reason", payload.get("reason"), inline=False),
            _field("Attempt", job.get("attempts")),
            _field("Next retry", job.get("next_attempt_at")),
        ]
    elif event == "download.exhausted":
        title, color = f"Gave up on: {label}", _COLOR_RED
        fields = [
            _field("Reason", payload.get("reason"), inline=False),
            _field("Attempts", job.get("attempts")),
        ]
    elif event == "series.added":
        year = series.get("start_year")
        name = f"{series.get('title')} ({year})" if year else series.get("title")
        title, color = f"Added series: {name}", _COLOR_PURPLE
        fields = [
            _field("Publisher", series.get("publisher")),
            _field("Subscribed", "Yes" if series.get("subscribed") else "No"),
            _field("Auto-download", "Yes" if series.get("auto_download") else "No"),
        ]
    elif event == "test":
        title, color = "PullBox webhook test", _COLOR_GREY
        fields = [_field("Message", payload.get("message"), inline=False)]
    else:
        title, color = f"PullBox: {event}", _COLOR_GREY

    embed: dict[str, Any] = {
        "title": title[:256],
        "color": color,
        "timestamp": _iso(timestamp),
        "footer": {"text": f"PullBox · {event}"},
        "fields": [f for f in fields if f is not None],
    }
    if thumbnail:
        embed["thumbnail"] = {"url": thumbnail}

    return {"username": "PullBox", "embeds": [embed]}


# ── Delivery ─────────────────────────────────────────────────────────────────


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(float(raw), MAX_RETRY_AFTER)
    except ValueError:
        return None


async def deliver(
    target: WebhookTarget,
    event: str,
    payload: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
    timestamp: datetime | None = None,
) -> DeliveryResult:
    """POST one event to one target. Never raises.

    Retries on connection errors, 5xx and 429 (honouring a short Retry-After);
    any other status is final. ``client`` is injectable for tests.
    """
    timestamp = timestamp or datetime.now(tz=timezone.utc)
    body_bytes = json.dumps(
        build_body(target.format, event, payload, timestamp), default=str
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-PullBox-Event": event,
        "X-PullBox-Delivery": uuid.uuid4().hex,
    }
    if target.secret:
        headers["X-PullBox-Signature"] = sign(target.secret, body_bytes)

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False)

    last: DeliveryResult = DeliveryResult(False, "No attempt made")
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            delay: float | None = None
            try:
                response = await client.post(target.url, content=body_bytes, headers=headers)
            except httpx.HTTPError as exc:
                last = DeliveryResult(False, f"{type(exc).__name__}: {exc}"[:500])
            else:
                if response.is_success:
                    return DeliveryResult(
                        True, f"HTTP {response.status_code}", response.status_code
                    )
                snippet = response.text.strip().replace("\n", " ")[:200]
                last = DeliveryResult(
                    False,
                    f"HTTP {response.status_code}" + (f": {snippet}" if snippet else ""),
                    response.status_code,
                )
                if response.status_code == 429:
                    delay = _retry_after(response)
                elif response.status_code < 500:
                    return last  # 4xx other than 429 will not get better on retry

            if attempt < MAX_ATTEMPTS:
                if delay is None:
                    delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
                logger.debug(
                    "webhook %r attempt %d/%d failed (%s); retrying in %.1fs",
                    target.name,
                    attempt,
                    MAX_ATTEMPTS,
                    last.message,
                    delay,
                )
                await asyncio.sleep(delay)
    finally:
        if own_client:
            await client.aclose()
    return last


# ── Dispatch ─────────────────────────────────────────────────────────────────

# asyncio only holds weak references to tasks; keep ours alive until they finish.
_inflight: set[asyncio.Task] = set()


def emit(event: str, payload: dict[str, Any]) -> asyncio.Task | None:
    """Schedule delivery of ``event`` to every enabled hook subscribed to it.

    Returns immediately (the task, or None when nothing could be scheduled).
    Safe to call from any coroutine — never from inside an open write
    transaction's critical section, only after commit.
    """
    if event not in EVENTS:
        logger.warning("webhooks.emit: unknown event %r ignored", event)
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("webhooks.emit: no running event loop; %s not delivered", event)
        return None
    task = loop.create_task(dispatch(event, payload), name=f"webhook:{event}")
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)
    return task


async def load_targets(event: str) -> list[WebhookTarget]:
    """Enabled hooks subscribed to ``event``, detached from the session."""
    from sqlalchemy import select  # noqa: PLC0415

    import pullbox.database as db_module  # noqa: PLC0415
    from pullbox.models import Webhook  # noqa: PLC0415

    if db_module.AsyncSessionLocal is None:
        return []
    async with db_module.AsyncSessionLocal() as db:
        rows = (
            (await db.execute(select(Webhook).where(Webhook.enabled == True)))  # noqa: E712
            .scalars()
            .all()
        )
        return [
            WebhookTarget(id=w.id, name=w.name, url=w.url, format=w.format, secret=w.secret)
            for w in rows
            if event in (w.events or [])
        ]


async def record_result(webhook_id: int, result: DeliveryResult, at: datetime) -> None:
    """Persist the last-delivery columns in a short, standalone transaction."""
    import pullbox.database as db_module  # noqa: PLC0415
    from pullbox.models import Webhook  # noqa: PLC0415

    if db_module.AsyncSessionLocal is None:
        return
    try:
        async with db_module.AsyncSessionLocal() as db:
            row = await db.get(Webhook, webhook_id)
            if row is None:
                return  # deleted while the delivery was in flight
            row.last_delivery_at = at
            row.last_delivery_success = result.success
            row.last_delivery_error = None if result.success else result.message
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("webhooks: could not record result for hook %d", webhook_id, exc_info=True)


async def dispatch(event: str, payload: dict[str, Any]) -> list[DeliveryResult]:
    """Deliver ``event`` to all subscribed hooks and record each outcome."""
    try:
        targets = await load_targets(event)
    except Exception:  # noqa: BLE001
        logger.exception("webhooks: failed loading targets for %s", event)
        return []
    if not targets:
        return []

    timestamp = datetime.now(tz=timezone.utc)
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        results = await asyncio.gather(
            *(deliver(t, event, payload, client=client, timestamp=timestamp) for t in targets),
            return_exceptions=True,
        )

    final: list[DeliveryResult] = []
    for target, result in zip(targets, results):
        if isinstance(result, BaseException):
            # deliver() swallows httpx errors; anything else is a bug worth a trace.
            logger.exception("webhooks: unexpected error delivering %s to %r", event, target.name)
            result = DeliveryResult(False, f"{type(result).__name__}: {result}"[:500])
        final.append(result)
        if result.success:
            logger.info("webhook %r: delivered %s (%s)", target.name, event, result.message)
        else:
            logger.warning(
                "webhook %r: %s delivery failed — %s", target.name, event, result.message
            )
        if target.id is not None:
            await record_result(target.id, result, datetime.now(tz=timezone.utc))
    return final


async def wait_for_inflight(timeout: float = 15.0) -> None:
    """Let in-flight deliveries finish (used at shutdown). Never raises."""
    if not _inflight:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*_inflight, return_exceptions=True), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
