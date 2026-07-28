"""App settings endpoints: general (library path) and post-download processing."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from pullbox.config import Settings
from pullbox.deps import DbDep, SettingsDep
from pullbox.models import GeneralSettings, PostProcessingSettings
from pullbox.schemas import (
    GeneralSettingsResponse,
    GeneralSettingsUpdate,
    PostProcessingPreviewRequest,
    PostProcessingPreviewResponse,
    PostProcessingSettingsResponse,
    PostProcessingSettingsUpdate,
)
from pullbox.services.general import (
    describe_path,
    get_or_create_general_settings,
    is_absolute_path,
    resolve_library_path,
)
from pullbox.services.postprocess import COMIC_EXTENSIONS, render_relative_path

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ── General ───────────────────────────────────────────────────────────────────


def _general_response(row: GeneralSettings, settings: Settings) -> GeneralSettingsResponse:
    """Build the response, resolving the effective path and probing it."""
    effective = (row.library_path or "").strip() or settings.library_path
    exists, writable = describe_path(effective)
    return GeneralSettingsResponse(
        id=row.id,
        library_path=row.library_path,
        effective_path=effective,
        config_library_path=settings.library_path,
        exists=exists,
        writable=writable,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/general", response_model=GeneralSettingsResponse)
async def get_general(db: DbDep, settings: SettingsDep):
    row = await get_or_create_general_settings(db)
    return _general_response(row, settings)


@router.patch("/general", response_model=GeneralSettingsResponse)
async def update_general(body: GeneralSettingsUpdate, db: DbDep, settings: SettingsDep):
    row = await get_or_create_general_settings(db)
    fields = body.model_dump(exclude_unset=True)

    if "library_path" in fields:
        value = (fields["library_path"] or "").strip()
        if value and not is_absolute_path(value):
            raise HTTPException(
                status_code=400,
                detail="Library path must be absolute (e.g. /comics or C:\\Comics)",
            )
        # Blank clears the override so the config file's value takes over again.
        row.library_path = value or None

    row.updated_at = datetime.now(tz=timezone.utc)
    await db.flush()
    await db.refresh(row)
    return _general_response(row, settings)


# ── Post-download processing ──────────────────────────────────────────────────


async def _get_or_create_settings(db: DbDep) -> PostProcessingSettings:
    """Return the singleton post-processing settings row, creating defaults once."""
    from sqlalchemy import select

    row = (await db.execute(select(PostProcessingSettings).limit(1))).scalar_one_or_none()
    if row is None:
        row = PostProcessingSettings()
        db.add(row)
        await db.flush()
        await db.refresh(row)
    return row


@router.get("/post-processing", response_model=PostProcessingSettingsResponse)
async def get_post_processing(db: DbDep):
    row = await _get_or_create_settings(db)
    return PostProcessingSettingsResponse.model_validate(row)


@router.patch("/post-processing", response_model=PostProcessingSettingsResponse)
async def update_post_processing(body: PostProcessingSettingsUpdate, db: DbDep):
    row = await _get_or_create_settings(db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.updated_at = datetime.now(tz=timezone.utc)
    await db.flush()
    await db.refresh(row)
    return PostProcessingSettingsResponse.model_validate(row)


@router.post("/post-processing/preview", response_model=PostProcessingPreviewResponse)
async def preview_post_processing(
    body: PostProcessingPreviewRequest, db: DbDep, settings: SettingsDep
):
    """Render a sample target path from the given (or saved) patterns."""
    row = await _get_or_create_settings(db)

    folder_pattern = body.folder_pattern if body.folder_pattern is not None else row.folder_pattern
    file_pattern = body.file_pattern if body.file_pattern is not None else row.file_pattern
    dest_root = body.destination_root if body.destination_root is not None else row.destination_root

    ext = (body.ext or "cbz").lstrip(".")
    tokens = {
        "series": body.series or "Batman",
        "publisher": body.publisher or "DC Comics",
        "year": str(body.year) if body.year is not None else "2016",
        "issue": body.issue or "12",
        "title": body.title or "The Return",
        "ext": ext,
    }

    relative = render_relative_path(folder_pattern, file_pattern, tokens)
    name = relative.as_posix()
    if relative.suffix.lower() not in COMIC_EXTENSIONS:
        name = f"{name}.{ext}"
    root = (dest_root or "").strip() or await resolve_library_path(db, settings.library_path)
    path = f"{root.rstrip('/')}/{name}"
    return PostProcessingPreviewResponse(path=path)
