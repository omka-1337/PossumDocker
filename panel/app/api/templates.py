from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import Providers, Templates, require_template
from app.games.providers import ProviderError
from app.games.schema import Option, Port, SecretField, SelectField, TemplateField
from app.games.validation import dependency_params

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateSummary(BaseModel):
    id: str
    name: str
    description: str | None
    icon: str | None


class TemplateDetail(TemplateSummary):
    """What the create-server form needs. Install/runtime details stay on the server."""

    fields: list[TemplateField]
    ports: list[Port]


@router.get("")
async def list_templates(templates: Templates) -> list[TemplateSummary]:
    return [TemplateSummary.model_validate(t, from_attributes=True) for t in templates.values()]


@router.get("/{template_id}")
async def get_template(template_id: str, templates: Templates) -> TemplateDetail:
    template = require_template(templates, template_id)
    fields = [f for f in template.fields if not (isinstance(f, SecretField) and f.hidden)]
    return TemplateDetail(
        id=template.id,
        name=template.name,
        description=template.description,
        icon=template.icon,
        fields=fields,
        ports=template.ports,
    )


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
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
