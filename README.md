<p align="center"><img src="web/public/logo.png" alt="" width="128"></p>

# PossumDocker

[![CI](https://github.com/omka-1337/PossumDocker/actions/workflows/ci.yml/badge.svg?branch=dev)](https://github.com/omka-1337/PossumDocker/actions/workflows/ci.yml)

Self-hosted panel for creating and running game servers — from Minecraft to Counter-Strike 1.6 — in Docker containers.

**Status:** early development.

## How it works

- **Panel** (`panel/`, Python/FastAPI) — web API, users and permissions, database.
- **Web UI** (`web/`, React + TypeScript + Vite), served by the panel.
- **Agent** (`agent/`, Go) — the only component with access to Docker. The panel sends it container specs and
  file operations over HTTP with a shared token; the agent listens on an internal network only the panel can reach.
- **Game templates** (`templates/*.yaml`) — describe a game: the create-server form, ports, install step, runtime
  container, config files, backups. Adding a game means adding a template, not code.

## Install

Needs Docker with Compose, `make` and `git` on a Linux machine.

```bash
git clone https://github.com/omka-1337/PossumDocker.git
cd PossumDocker
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

Everything the panel keeps (database, backups) is in `data/`. Game servers live in Docker volumes named `possum-<id>-data`.

Behind a reverse proxy (nginx, Caddy), add its address to `.env`, e.g. `POSSUM_TRUSTED_PROXIES=172.17.0.1`:
the panel then trusts its `X-Forwarded-For`/`X-Forwarded-Proto` headers, so login throttling sees visitors' real
addresses and the session cookie is marked secure on HTTPS.

Before exposing the panel to the internet, read [SECURITY.md](SECURITY.md): use HTTPS, and only make people
administrators you would trust with the machine.

## Development

### Panel

Requires Python 3.12+ and, without an agent, access to Docker (the user must be able to run `docker ps`).
Game servers get containers named `possum-<id>` and volumes named `possum-<id>-data`.
The file browser starts a small `busybox` helper (`possum-<id>-files`, no network) that is removed when idle.

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
`POSSUM_DATABASE_URL=postgresql+asyncpg://user:pass@host/db`.

Settings (environment variables):

| Variable | Default | |
|---|---|---|
| `POSSUM_DATABASE_URL` | `sqlite+aiosqlite:///data/panel.db` | database |
| `POSSUM_BACKUPS_DIR` | `data/backups` | server backups; ideally on another disk |
| `POSSUM_TIMEZONE` | the machine's (`/etc/localtime`) | time zone schedules run in, e.g. `Europe/Kyiv` |
| `POSSUM_CACHE_DIR` | `data/cache` | downloaded game art, safe to delete |

New migration after changing `app/models.py`:

```bash
.venv/bin/alembic revision --autogenerate -m "describe the change"
```

### Agent

Requires Go 1.27+ (or build it with Docker: `docker build agent/`).

```bash
cd agent
go test ./...
POSSUM_AGENT_TOKEN=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n') POSSUM_AGENT_LISTEN=127.0.0.1:8081 go run ./cmd/agent
```

Point a development panel at it with `POSSUM_AGENT_URL=http://127.0.0.1:8081` and the same `POSSUM_AGENT_TOKEN`.
Without `POSSUM_AGENT_URL` the panel talks to Docker itself, which is only meant for development.

### Web UI

Requires Node.js 22+. Run the panel first, then:

```bash
cd web
npm install
npm run dev     # http://localhost:5173, /api is proxied to the panel
npm run build   # type-check + production build into web/dist
npm run lint
```

If the panel is not on `localhost:8080`, set `POSSUM_PANEL_URL=http://host:port`.

## License

[AGPL-3.0](LICENSE)
