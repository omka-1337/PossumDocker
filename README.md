# DockerGameServer

> Working title.

Self-hosted panel for creating and running game servers — from Minecraft to Counter-Strike 1.6 — in Docker containers.

**Status:** early development.

## How it works

- **Panel** (`panel/`, Python/FastAPI) — web API, users, database.
- **Web UI** (`web/`, React + TypeScript + Vite).
- **Agent** (`agent/`, Go) — the only component with access to Docker — *planned before the first release*.
- **Game templates** (`templates/*.yaml`) — describe a game: the create-server form, ports, install step and runtime container. Adding a game means adding a template, not code.

## Install

Needs Docker with Compose, `make` and `git` on a Linux machine.

```bash
git clone https://github.com/omka-1337/DockerGameServer.git
cd DockerGameServer
make start
```

The first `make start` builds the panel, asks for an administrator account and prints the address
(port 8080 by default, change it in `.env`).

| Command | |
|---|---|
| `make start` | build and start the panel |
| `make stop` | stop the panel; game servers keep running |
| `make update` | pull the latest version from GitHub and restart |
| `make logs` | follow the panel's logs |
| `make admin` | add an administrator or reset a forgotten password |

Everything the panel keeps (database, backups) is in `data/`. Game servers live in Docker volumes named `dgs-<id>-data`.

## Development

### Panel

Requires Python 3.12+ and access to Docker (the user must be able to run `docker ps`).
Game servers get containers named `dgs-<id>` and volumes named `dgs-<id>-data`.
The file browser starts a small `busybox` helper (`dgs-<id>-files`, no network) that is removed when idle.

```bash
cd panel
python -m venv .venv
.venv/bin/pip install -e ".[dev]"

# --reload-dir/--reload-include: restart on template changes too, not only on Python code
.venv/bin/uvicorn app.main:app --port 8080 --reload --reload-dir app --reload-dir ../templates --reload-include '*.yaml'
# API docs: http://localhost:8080/docs
.venv/bin/pytest
.venv/bin/ruff check .
```

Data is stored in `data/panel.db` (SQLite) by default; migrations run on startup.
Use Postgres instead with `pip install -e ".[postgres]"` and
`DGS_DATABASE_URL=postgresql+asyncpg://user:pass@host/db`.

Settings (environment variables):

| Variable | Default | |
|---|---|---|
| `DGS_DATABASE_URL` | `sqlite+aiosqlite:///data/panel.db` | database |
| `DGS_BACKUPS_DIR` | `data/backups` | server backups; ideally on another disk |
| `DGS_TIMEZONE` | the machine's (`/etc/localtime`) | time zone schedules run in, e.g. `Europe/Kyiv` |
| `DGS_CACHE_DIR` | `data/cache` | downloaded game art, safe to delete |

New migration after changing `app/models.py`:

```bash
.venv/bin/alembic revision --autogenerate -m "describe the change"
```

### Web UI

Requires Node.js 22+. Run the panel first, then:

```bash
cd web
npm install
npm run dev     # http://localhost:5173, /api is proxied to the panel
npm run build   # type-check + production build into web/dist
npm run lint
```

If the panel is not on `localhost:8080`, set `DGS_PANEL_URL=http://host:port`.

## License

[AGPL-3.0](LICENSE)
