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
    # Server backups (<server id>/<backup id>.tar.gz). Put it on another disk if you can.
    backups_dir: Path = REPO_ROOT / "data" / "backups"
    # Time zone schedules run in, e.g. "Europe/Kyiv". Default: the machine's.
    timezone: str | None = None
    # The built web UI (web/dist). Served by the panel when it exists; in development Vite serves it.
    web_dir: Path = REPO_ROOT / "web" / "dist"
    # The Go agent that runs containers. Without it the panel talks to Docker itself (development only).
    agent_url: str | None = None
    agent_token: str | None = None
    # How long dynamic select options (game versions etc.) are cached, in seconds.
    options_cache_ttl: int = 3600
