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


# ── Step 5.3 — Newznab connection test ───────────────────────────────────────


@router.post("/{indexer_id}/test", response_model=IndexerTestResponse)
async def test_indexer(indexer_id: int, db: DbDep):
    indexer = await _get_indexer_or_404(indexer_id, db)

    if indexer.type not in ("newznab", "nzbhydra2"):
        raise HTTPException(
            status_code=400,
            detail="Test not supported for this indexer type",
        )

    caps_url = f"{indexer.url.rstrip('/')}/api"
    params: dict[str, str] = {"t": "caps"}
    if indexer.api_key:
        params["apikey"] = indexer.api_key

    success = False
    message = ""

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(caps_url, params=params)

        if resp.status_code == 200:
            try:
                root = etree.fromstring(resp.content)
                # Handle both bare <caps> and namespaced <ns:caps>
                tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
                if tag == "caps":
                    success = True
                    message = "Connection successful"
                else:
                    message = f"Unexpected root element: <{tag}>"
            except etree.XMLSyntaxError as exc:
                message = f"Invalid XML: {exc}"
        else:
            message = f"HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        message = f"Connection error: {exc}"

    indexer.last_tested_at = datetime.utcnow()
    indexer.last_test_success = success
    await db.flush()

    return IndexerTestResponse(success=success, message=message)
