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
    # Metron credentials (https://metron.cloud/ — requires a registered account;
    # HTTP Basic auth, not an API key). When set, Metron is the primary metadata
    # source and ComicVine (if its key is set) is used only as a fallback.
    metron_username: str = ""
    metron_password: str = ""
    # Which provider is primary: "metron" (default) or "comicvine". The other, if
    # configured, is used as a per-operation fallback.
    metadata_provider: str = "metron"
    retry_time: str = "06:00"
    max_retries: int = 20
    pull_list_lookahead_weeks: int = 2
    # ComicVine rate limiting (shared across all callers). ComicVine caps ~200
    # requests/hour per resource and throttles rapid bursts; keep headroom.
    comicvine_min_interval: float = 1.0
    comicvine_rate_limit_per_hour: int = 190
    # Metron rate limiting (shared across all callers). Metron caps 20 requests/min
    # (burst) and 5000/day (sustained); keep headroom under both.
    metron_rate_limit_per_min: int = 18
    metron_rate_limit_per_day: int = 4800
    # Background ComicVine backfill for imported issues: how often a batch runs
    # and how many un-synced issues to sample per batch (see sync_imported_issues).
    import_sync_interval_minutes: int = 5
    import_sync_batch_size: int = 10
    secret_key: str = "changeme"
    debug: bool = False

    model_config = {"env_prefix": "PULLBOX_"}

    @property
    def metadata_configured(self) -> bool:
        """True when at least one metadata source (Metron or ComicVine) has credentials."""
        return bool(
            (self.metron_username and self.metron_password) or self.comicvine_api_key
        )

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
