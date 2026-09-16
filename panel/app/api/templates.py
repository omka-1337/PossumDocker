from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import Providers, Templates, require_template
from app.games.art import MEDIA_TYPES, ArtCache
from app.games.providers import InvalidParams, ProviderError
from app.games.registry import icon_path
from app.games.schema import (
    ConsoleSpec,
    Option,
    Port,
    SecretField,
    SelectField,
    Template,
    TemplateField,
    TemplateGroup,
)
from app.games.validation import dependency_params

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateSummary(BaseModel):
    id: str
    name: str
    description: str | None
    # Icon from the web (template `art.icon` or Steam); may fail to load, then the UI uses icon_url.
    remote_icon_url: str | None
    # Icon bundled with the template. None: the UI shows a generic one.
    icon_url: str | None
    # Wide cover art from the web (template `art.cover` or the Steam header).
    cover_url: str | None
    color: str | None
    group: TemplateGroup | None


class TemplateDetail(TemplateSummary):
    """What the create-server form needs. Install/runtime details stay on the server."""

    fields: list[TemplateField]
    ports: list[Port]
    console: ConsoleSpec


def summary(template: Template) -> dict:
    base = f"/api/templates/{template.id}"
    steam = template.steam_appid is not None
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "remote_icon_url": f"{base}/art/icon" if template.art.icon or steam else None,
        "icon_url": f"{base}/icon" if template.icon else None,
        "cover_url": f"{base}/art/cover" if template.art.cover or steam else None,
        "color": template.color,
        "group": template.group,
    }


@router.get("")
async def list_templates(templates: Templates) -> list[TemplateSummary]:
    return [TemplateSummary(**summary(t)) for t in templates.values()]


@router.get("/{template_id}")
async def get_template(template_id: str, templates: Templates) -> TemplateDetail:
    template = require_template(templates, template_id)
    fields = [f for f in template.fields if not (isinstance(f, SecretField) and f.hidden)]
    return TemplateDetail(**summary(template), fields=fields, ports=template.ports, console=template.console)


# An SVG opened directly is a document that could run scripts: allow none, whatever its source.
IMAGE_HEADERS = {
    "Cache-Control": "public, max-age=86400",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}


@router.get("/{template_id}/icon")
async def get_template_icon(template_id: str, request: Request, templates: Templates) -> FileResponse:
    path = icon_path(request.app.state.templates_dir, require_template(templates, template_id))
    if path is None:
        raise HTTPException(404, "this game has no icon")
    return FileResponse(path, headers=IMAGE_HEADERS)


@router.get("/{template_id}/art/{kind}")
async def get_remote_art(
    template_id: str, kind: Literal["icon", "cover"], request: Request, templates: Templates
) -> FileResponse:
    """The template's own link first, then Steam: the first source that has the image wins."""
    template = require_template(templates, template_id)
    art: ArtCache = request.app.state.art
    path = None
    if url := getattr(template.art, kind):
        path = await art.from_url(url)
    if path is None and template.steam_appid is not None:
        path = await art.from_steam(template.steam_appid, kind)
    if path is None:
        raise HTTPException(404, f"no {kind} for this game right now")
    return FileResponse(path, media_type=MEDIA_TYPES[path.suffix[1:]], headers=IMAGE_HEADERS)


@router.get("/{template_id}/fields/{field_id}/options")
async def get_field_options(
    template_id: str, field_id: str, request: Request, templates: Templates, providers: Providers
) -> list[Option]:
    """Options for a select. Values of `depends_on` fields come as query params: ?loader=paper"""
    template = require_template(templates, template_id)
    field = template.field(field_id)
    if not isinstance(field, SelectField):
        raise HTTPException(404, f"'{field_id}' is not a select field")
    if not field.options_from:
        return field.options

    params = dependency_params(field, dict(request.query_params))
    try:
        return await providers.get(field.options_from, params)
    except InvalidParams as exc:
        raise HTTPException(422, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
