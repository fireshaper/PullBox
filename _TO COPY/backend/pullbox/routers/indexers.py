"""Indexer CRUD and Newznab connection-test endpoints."""

from __future__ import annotations

from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Response
from lxml import etree
from sqlalchemy import select

from pullbox.deps import DbDep
from pullbox.models import Indexer
from pullbox.schemas import (
    IndexerCreate,
    IndexerResponse,
    IndexerTestRequest,
    IndexerTestResponse,
    IndexerUpdate,
)

router = APIRouter(prefix="/api/indexers", tags=["indexers"])


async def _get_indexer_or_404(indexer_id: int, db) -> Indexer:
    row = await db.get(Indexer, indexer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Indexer not found")
    return row


# ── Step 5.2 — CRUD ──────────────────────────────────────────────────────────


@router.post("/", response_model=IndexerResponse, status_code=201)
async def create_indexer(body: IndexerCreate, db: DbDep):
    indexer = Indexer(**body.model_dump())
    db.add(indexer)
    await db.flush()
    await db.refresh(indexer)
    return IndexerResponse.model_validate(indexer)


@router.get("/", response_model=list[IndexerResponse])
async def list_indexers(db: DbDep):
    rows = (
        await db.execute(select(Indexer).order_by(Indexer.priority))
    ).scalars().all()
    return [IndexerResponse.model_validate(i) for i in rows]


@router.get("/{indexer_id}", response_model=IndexerResponse)
async def get_indexer(indexer_id: int, db: DbDep):
    indexer = await _get_indexer_or_404(indexer_id, db)
    return IndexerResponse.model_validate(indexer)


@router.patch("/{indexer_id}", response_model=IndexerResponse)
async def update_indexer(indexer_id: int, body: IndexerUpdate, db: DbDep):
    indexer = await _get_indexer_or_404(indexer_id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(indexer, field, value)
    await db.flush()
    await db.refresh(indexer)
    return IndexerResponse.model_validate(indexer)


@router.delete("/{indexer_id}", status_code=204)
async def delete_indexer(indexer_id: int, db: DbDep):
    indexer = await _get_indexer_or_404(indexer_id, db)
    await db.delete(indexer)
    await db.flush()
    return Response(status_code=204)


# ── Step 5.3 — Indexer connection tests ──────────────────────────────────────

_TESTABLE_TYPES = ("newznab", "nzbhydra2", "prowlarr", "jackett")


async def _run_indexer_test(type_: str, url: str, api_key: str | None) -> tuple[bool, str]:
    """Probe an indexer's connection. Returns (success, message)."""
    if type_ in ("newznab", "nzbhydra2"):
        return await _test_torznab_caps(f"{url.rstrip('/')}/api", api_key)
    if type_ == "jackett":
        if not api_key:
            return False, "Jackett requires an API key"
        caps_url = f"{url.rstrip('/')}/api/v2.0/indexers/all/results/torznab/api"
        return await _test_torznab_caps(caps_url, api_key)
    if type_ == "prowlarr":
        return await _test_prowlarr(url, api_key)
    raise HTTPException(
        status_code=400,
        detail="Test not supported for this indexer type",
    )


async def _test_torznab_caps(caps_url: str, api_key: str | None) -> tuple[bool, str]:
    """Confirm a Newznab/Torznab endpoint answers a caps query with <caps> XML."""
    params: dict[str, str] = {"t": "caps"}
    if api_key:
        params["apikey"] = api_key

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(caps_url, params=params)

        if resp.status_code == 200:
            try:
                root = etree.fromstring(resp.content)
                # Handle both bare <caps> and namespaced <ns:caps>
                tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
                if tag == "caps":
                    return True, "Connection successful"
                return False, f"Unexpected root element: <{tag}>"
            except etree.XMLSyntaxError as exc:
                return False, f"Invalid XML: {exc}"
        return False, f"HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Connection error: {exc}"


async def _test_prowlarr(url: str, api_key: str | None) -> tuple[bool, str]:
    """Confirm Prowlarr is reachable and the API key authenticates."""
    if not api_key:
        return False, "Prowlarr requires an API key"

    status_url = f"{url.rstrip('/')}/api/v1/system/status"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(status_url, headers={"X-Api-Key": api_key})
    except Exception as exc:  # noqa: BLE001
        return False, f"Connection error: {exc}"

    if resp.status_code == 200:
        return True, "Connection successful"
    if resp.status_code == 401:
        return False, "Invalid API key"
    return False, f"HTTP {resp.status_code}"


@router.post("/test", response_model=IndexerTestResponse)
async def test_indexer_config(body: IndexerTestRequest):
    """Test an unsaved indexer config (used by the add/edit dialog)."""
    success, message = await _run_indexer_test(body.type, body.url, body.api_key)
    return IndexerTestResponse(success=success, message=message)


@router.post("/{indexer_id}/test", response_model=IndexerTestResponse)
async def test_indexer(indexer_id: int, db: DbDep):
    indexer = await _get_indexer_or_404(indexer_id, db)

    success, message = await _run_indexer_test(
        indexer.type, indexer.url, indexer.api_key
    )

    indexer.last_tested_at = datetime.utcnow()
    indexer.last_test_success = success
    await db.flush()

    return IndexerTestResponse(success=success, message=message)
