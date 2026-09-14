import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import router
from app.core.config import Settings
from app.core.db import create_engine, run_migrations
from app.games.providers import OptionsProviders, default_providers
from app.games.registry import load_templates


def create_app(settings: Settings | None = None, providers: OptionsProviders | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await run_migrations(settings.database_url)
        engine = create_engine(settings.database_url)

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            app.state.providers = providers or default_providers(client, settings.options_cache_ttl)
            app.state.templates = load_templates(settings.templates_dir, app.state.providers)
            app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
            yield

        await engine.dispose()

    app = FastAPI(title="DockerGameServer Panel", lifespan=lifespan)
    app.include_router(router)
    return app


logging.basicConfig(level=logging.INFO)
app = create_app()
