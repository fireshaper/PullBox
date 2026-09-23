"""Webhook settings: CRUD, event catalogue and test deliveries."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select

from pullbox.deps import DbDep
from pullbox.models import Webhook
from pullbox.schemas import (
    WebhookCreate,
    WebhookEventInfo,
    WebhookResponse,
    WebhookTestRequest,
    WebhookTestResponse,
    WebhookUpdate,
)
from pullbox.services import webhooks as svc

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


async def _get_webhook_or_404(webhook_id: int, db: DbDep) -> Webhook:
    hook = await db.get(Webhook, webhook_id)
    if hook is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return hook


# Static routes first so "/events" and "/test" are never captured by "/{webhook_id}".


@router.get("/events", response_model=list[WebhookEventInfo])
async def list_events():
    """The events a webhook can subscribe to (drives the Settings UI checklist)."""
    return [WebhookEventInfo(name=k, description=v) for k, v in svc.EVENTS.items()]


@router.post("/test", response_model=WebhookTestResponse)
async def test_unsaved(body: WebhookTestRequest):
    """Send a ``test`` event to an unsaved config (used by the add/edit dialog)."""
    target = svc.WebhookTarget(
        id=None, name=body.name, url=body.url, format=body.format, secret=body.secret
    )
    result = await svc.deliver(target, "test", svc.build_test_payload(body.name))
    return WebhookTestResponse(
        success=result.success, message=result.message, status_code=result.status_code
    )


@router.post("/", response_model=WebhookResponse, status_code=201)
async def create_webhook(body: WebhookCreate, db: DbDep):
    hook = Webhook(**body.model_dump())
    db.add(hook)
    await db.flush()
    await db.refresh(hook)
    return WebhookResponse.model_validate(hook)


@router.get("/", response_model=list[WebhookResponse])
async def list_webhooks(db: DbDep):
    result = await db.execute(select(Webhook).order_by(Webhook.id))
    return [WebhookResponse.model_validate(h) for h in result.scalars().all()]


@router.get("/{webhook_id}", response_model=WebhookResponse)
async def get_webhook(webhook_id: int, db: DbDep):
    hook = await _get_webhook_or_404(webhook_id, db)
    return WebhookResponse.model_validate(hook)


@router.patch("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(webhook_id: int, body: WebhookUpdate, db: DbDep):
    hook = await _get_webhook_or_404(webhook_id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(hook, field, value)
    await db.flush()
    await db.refresh(hook)
    return WebhookResponse.model_validate(hook)


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(webhook_id: int, db: DbDep):
    hook = await _get_webhook_or_404(webhook_id, db)
    await db.delete(hook)
    await db.flush()
    return Response(status_code=204)


@router.post("/{webhook_id}/test", response_model=WebhookTestResponse)
async def test_webhook(webhook_id: int, db: DbDep):
    """Send a ``test`` event to a saved hook and record the outcome on it.

    The delivery happens with no write pending: the row is only read before the
    HTTP call, and the outcome columns are set after it returns.
    """
    hook = await _get_webhook_or_404(webhook_id, db)
    target = svc.WebhookTarget(
        id=hook.id, name=hook.name, url=hook.url, format=hook.format, secret=hook.secret
    )

    result = await svc.deliver(target, "test", svc.build_test_payload(hook.name))

    hook.last_delivery_at = datetime.now(tz=timezone.utc)
    hook.last_delivery_success = result.success
    hook.last_delivery_error = None if result.success else result.message
    await db.flush()

    return WebhookTestResponse(
        success=result.success, message=result.message, status_code=result.status_code
    )
