"""Download client settings: CRUD and connection test endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response

from pullbox.clients.nzbget import NZBGetClient
from pullbox.deps import DbDep
from pullbox.models import DownloadClient
from pullbox.schemas import (
    DownloadClientCreate,
    DownloadClientResponse,
    DownloadClientTestResponse,
    DownloadClientUpdate,
)

router = APIRouter(prefix="/api/download-clients", tags=["download-clients"])


async def _get_client_or_404(client_id: int, db: DbDep) -> DownloadClient:
    dc = await db.get(DownloadClient, client_id)
    if dc is None:
        raise HTTPException(status_code=404, detail="Download client not found")
    return dc


@router.post("/", response_model=DownloadClientResponse, status_code=201)
async def create_client(body: DownloadClientCreate, db: DbDep):
    dc = DownloadClient(**body.model_dump())
    db.add(dc)
    await db.flush()
    await db.refresh(dc)
    return DownloadClientResponse.model_validate(dc)


@router.get("/", response_model=list[DownloadClientResponse])
async def list_clients(db: DbDep):
    from sqlalchemy import select

    result = await db.execute(select(DownloadClient).order_by(DownloadClient.id))
    return [DownloadClientResponse.model_validate(dc) for dc in result.scalars().all()]


@router.get("/{client_id}", response_model=DownloadClientResponse)
async def get_client(client_id: int, db: DbDep):
    dc = await _get_client_or_404(client_id, db)
    return DownloadClientResponse.model_validate(dc)


@router.patch("/{client_id}", response_model=DownloadClientResponse)
async def update_client(client_id: int, body: DownloadClientUpdate, db: DbDep):
    dc = await _get_client_or_404(client_id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(dc, field, value)
    await db.flush()
    await db.refresh(dc)
    return DownloadClientResponse.model_validate(dc)


@router.delete("/{client_id}", status_code=204)
async def delete_client(client_id: int, db: DbDep):
    dc = await _get_client_or_404(client_id, db)
    await db.delete(dc)
    await db.flush()
    return Response(status_code=204)


@router.post("/{client_id}/test", response_model=DownloadClientTestResponse)
async def test_client(client_id: int, db: DbDep):
    dc = await _get_client_or_404(client_id, db)

    if dc.type != "nzbget":
        raise HTTPException(
            status_code=400,
            detail="Test not supported for this client type",
        )

    client = NZBGetClient(
        host=dc.host,
        port=dc.port,
        username=dc.username or "nzbget",
        password=dc.password or "",
    )

    now = datetime.now(tz=timezone.utc)
    try:
        success = await client.test_connection()
        message = "Connection successful" if success else "Connection failed"
    except Exception as exc:
        success = False
        message = f"Connection error: {exc}"

    dc.last_tested_at = now
    dc.last_test_success = success
    await db.flush()

    return DownloadClientTestResponse(success=success, message=message)
