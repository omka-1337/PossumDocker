from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PANEL_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = PANEL_DIR.parent


class Settings(BaseSettings):
    """Panel configuration. Every field can be overridden with a DGS_* env variable."""

    model_config = SettingsConfigDict(env_prefix="DGS_", env_file=".env", extra="ignore")

    # sqlite+aiosqlite:///path/to/panel.db  or  postgresql+asyncpg://user:pass@host/db
    database_url: str = f"sqlite+aiosqlite:///{REPO_ROOT / 'data' / 'panel.db'}"
    templates_dir: Path = REPO_ROOT / "templates"
    # Downloaded, regenerable files (game art from Steam...). Safe to delete.
    cache_dir: Path = REPO_ROOT / "data" / "cache"
    # How long dynamic select options (game versions etc.) are cached, in seconds.
    options_cache_ttl: int = 3600
