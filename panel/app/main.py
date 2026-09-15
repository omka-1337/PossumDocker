import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import router
from app.core.config import Settings
from app.core.db import create_engine, run_migrations
from app.games.art import ArtCache
from app.games.providers import OptionsProviders, default_providers
from app.games.registry import load_templates
from app.runtime.agent import AgentRuntime
from app.runtime.docker import DockerRuntime
from app.runtime.manager import Runtime, ServerManager
from app.runtime.scheduler import Scheduler, resolve_timezone


def create_app(
    settings: Settings | None = None,
    providers: OptionsProviders | None = None,
    runtime: Runtime | None = None,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await run_migrations(settings.database_url)
        engine = create_engine(settings.database_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        if runtime is not None:
            docker = runtime
        elif settings.agent_url:
            if not settings.agent_token:
                raise RuntimeError("DGS_AGENT_URL is set but DGS_AGENT_TOKEN isn't")
            docker = AgentRuntime(settings.agent_url, settings.agent_token)
        else:
            logging.getLogger(__name__).warning(
                "no DGS_AGENT_URL: talking to Docker directly (development only)"
            )
            docker = DockerRuntime()

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            app.state.providers = providers or default_providers(client, settings.options_cache_ttl)
            app.state.templates = load_templates(settings.templates_dir, app.state.providers)
            app.state.sessionmaker = sessionmaker
            app.state.templates_dir = settings.templates_dir
            app.state.art = ArtCache(client, settings.cache_dir)
            app.state.manager = ServerManager(
                docker, sessionmaker, app.state.templates, settings.templates_dir, settings.backups_dir
            )
            await app.state.manager.recover()
            app.state.manager.start_background_jobs()
            app.state.scheduler = Scheduler(
                app.state.manager, sessionmaker, resolve_timezone(settings.timezone)
            )
            app.state.scheduler.start()
            yield
            await app.state.scheduler.stop()
            await app.state.manager.shutdown()

        if runtime is None:
            await docker.close()
        await engine.dispose()

    app = FastAPI(title="DockerGameServer Panel", lifespan=lifespan)
    app.include_router(router)
    if (settings.web_dir / "index.html").is_file():
        serve_web(app, settings.web_dir)
    return app


def serve_web(app: FastAPI, web_dir: Path) -> None:
    """The built React app. Any path that isn't a file is a client-side route: answer with index.html."""
    root = web_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def web(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "not found")
        file = (root / path).resolve()
        if path and file.is_relative_to(root) and file.is_file():
            # Vite puts a content hash in asset names, so they can be cached for good.
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return FileResponse(file, headers={"Cache-Control": cache})
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})


logging.basicConfig(level=logging.INFO)
app = create_app()
