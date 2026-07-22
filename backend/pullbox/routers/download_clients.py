"""Download client settings: CRUD and connection test endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response

from pullbox.clients.nzbget import NZBGetClient
from pullbox.clients.qbittorrent import QBittorrentClient
from pullbox.clients.sabnzbd import SABnzbdClient
from pullbox.deps import DbDep
from pullbox.models import DownloadClient
from pullbox.schemas import (
    DownloadClientCreate,
    DownloadClientResponse,
    DownloadClientTestRequest,
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


async def _run_client_test(
    type_: str,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    api_key: str | None,
) -> tuple[bool, str]:
    """Probe a download client's connection. Returns (success, message)."""
    client: NZBGetClient | SABnzbdClient | QBittorrentClient
    if type_ == "nzbget":
        client = NZBGetClient(
            host=host,
            port=port,
            username=username or "nzbget",
            password=password or "",
        )
    elif type_ == "sabnzbd":
        if not api_key:
            return False, "SABnzbd requires an API key"
        client = SABnzbdClient(host=host, port=port, api_key=api_key)
    elif type_ == "qbittorrent":
        client = QBittorrentClient(
            host=host,
            port=port,
            username=username or "admin",
            password=password or "",
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="Test not supported for this client type",
        )

    try:
        success = await client.test_connection()
        return success, "Connection successful" if success else "Connection failed"
    except Exception as exc:
        return False, f"Connection error: {exc}"


@router.post("/test", response_model=DownloadClientTestResponse)
async def test_client_config(body: DownloadClientTestRequest):
    """Test an unsaved download-client config (used by the add/edit dialog)."""
    success, message = await _run_client_test(
        body.type, body.host, body.port, body.username, body.password, body.api_key
    )
    return DownloadClientTestResponse(success=success, message=message)


@router.post("/{client_id}/test", response_model=DownloadClientTestResponse)
async def test_client(client_id: int, db: DbDep):
    dc = await _get_client_or_404(client_id, db)

    now = datetime.now(tz=timezone.utc)
    success, message = await _run_client_test(
        dc.type, dc.host, dc.port, dc.username, dc.password, dc.api_key
    )

    dc.last_tested_at = now
    dc.last_test_success = success
    await db.flush()

    return DownloadClientTestResponse(success=success, message=message)
