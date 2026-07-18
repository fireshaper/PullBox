"""App settings endpoints. Currently: post-download processing configuration."""

from datetime import datetime, timezone

from fastapi import APIRouter

from pullbox.deps import DbDep, SettingsDep
from pullbox.models import PostProcessingSettings
from pullbox.schemas import (
    PostProcessingPreviewRequest,
    PostProcessingPreviewResponse,
    PostProcessingSettingsResponse,
    PostProcessingSettingsUpdate,
)
from pullbox.services.postprocess import COMIC_EXTENSIONS, render_relative_path

router = APIRouter(prefix="/api/settings", tags=["settings"])


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
    root = (dest_root or "").strip() or settings.library_path
    path = f"{root.rstrip('/')}/{name}"
    return PostProcessingPreviewResponse(path=path)
