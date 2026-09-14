from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.games.providers import OptionsProviders
from app.games.schema import Template
from app.runtime.manager import ServerManager

Session = Annotated[AsyncSession, Depends(get_session)]


def get_templates(request: Request) -> dict[str, Template]:
    return request.app.state.templates


def get_providers(request: Request) -> OptionsProviders:
    return request.app.state.providers


def get_manager(request: Request) -> ServerManager:
    return request.app.state.manager


Templates = Annotated[dict[str, Template], Depends(get_templates)]
Providers = Annotated[OptionsProviders, Depends(get_providers)]
Manager = Annotated[ServerManager, Depends(get_manager)]


def require_template(templates: dict[str, Template], template_id: str) -> Template:
    template = templates.get(template_id)
    if template is None:
        raise HTTPException(404, f"unknown game template '{template_id}'")
    return template
