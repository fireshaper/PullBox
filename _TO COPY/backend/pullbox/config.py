import os
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

# Project root is three levels up from this file (backend/pullbox/config.py → project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_CONFIG_SEARCH_PATHS = [
    "/config/config.yaml",  # Docker / Linux production
    str(_PROJECT_ROOT / "config" / "config.yaml"),  # Local dev (project root)
]


class YamlConfigSource(PydanticBaseSettingsSource):
    def get_field_value(self, field: Any, field_name: str) -> Any:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        explicit = os.environ.get("PULLBOX_CONFIG_FILE")
        candidates = [explicit] if explicit else _CONFIG_SEARCH_PATHS
        for path in candidates:
            if path and os.path.exists(path):
                with open(path) as f:
                    return yaml.safe_load(f) or {}
        return {}


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:////config/pullbox.db"
    library_path: str = "/comics"
    config_path: str = "/config"
    comicvine_api_key: str = ""
    retry_time: str = "06:00"
    max_retries: int = 20
    pull_list_lookahead_weeks: int = 2
    # ComicVine rate limiting (shared across all callers). ComicVine caps ~200
    # requests/hour per resource and throttles rapid bursts; keep headroom.
    comicvine_min_interval: float = 1.0
    comicvine_rate_limit_per_hour: int = 190
    # Background ComicVine backfill for imported issues: how often a batch runs
    # and how many un-synced issues to sample per batch (see sync_imported_issues).
    import_sync_interval_minutes: int = 5
    import_sync_batch_size: int = 10
    secret_key: str = "changeme"
    debug: bool = False

    model_config = {"env_prefix": "PULLBOX_"}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSource(settings_cls),
            file_secret_settings,
        )
