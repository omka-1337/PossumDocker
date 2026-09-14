# DockerGameServer

> Working title.

Self-hosted panel for creating and running game servers — from Minecraft to Counter-Strike 1.6 — in Docker containers.

**Status:** early development, nothing to install yet.

## How it works

- **Panel** (`panel/`, Python/FastAPI) — web API, users, database.
- **Web UI** (`web/`, React + TypeScript + Vite).
- **Agent** (`agent/`, Go) — the only component with access to Docker — *planned before the first release*.
- **Game templates** (`templates/*.yaml`) — describe a game: the create-server form, ports, install step and runtime container. Adding a game means adding a template, not code.

## Development

### Panel

Requires Python 3.12+.

```bash
cd panel
python -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/uvicorn app.main:app --reload --port 8080   # API docs: http://localhost:8080/docs
.venv/bin/pytest
.venv/bin/ruff check .
```

Data is stored in `data/panel.db` (SQLite) by default; migrations run on startup.
Use Postgres instead with `pip install -e ".[postgres]"` and
`DGS_DATABASE_URL=postgresql+asyncpg://user:pass@host/db`.

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
