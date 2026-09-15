import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import router
from app.core.config import Settings
from app.core.db import create_engine, run_migrations
from app.games.art import ArtCache
from app.games.providers import OptionsProviders, default_providers
from app.games.registry import load_templates
from app.runtime.docker import DockerRuntime
from app.runtime.manager import Runtime, ServerManager


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
        docker = runtime or DockerRuntime()

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            app.state.providers = providers or default_providers(client, settings.options_cache_ttl)
            app.state.templates = load_templates(settings.templates_dir, app.state.providers)
            app.state.sessionmaker = sessionmaker
            app.state.templates_dir = settings.templates_dir
            app.state.art = ArtCache(client, settings.cache_dir)
            app.state.manager = ServerManager(
                docker, sessionmaker, app.state.templates, settings.templates_dir
            )
            await app.state.manager.recover()
            app.state.manager.start_background_jobs()
            yield
            await app.state.manager.shutdown()

        if runtime is None:
            await docker.close()
        await engine.dispose()

    app = FastAPI(title="DockerGameServer Panel", lifespan=lifespan)
    app.include_router(router)
    return app


logging.basicConfig(level=logging.INFO)
app = create_app()
