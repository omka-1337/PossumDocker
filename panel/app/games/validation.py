"""Validate what the user filled in against a template's fields."""

import re
import secrets
from typing import Any

from app.games.providers import InvalidParams, OptionsProviders, ProviderError
from app.games.schema import (
    BooleanField,
    NumberField,
    SecretField,
    SelectField,
    StringField,
    Template,
    TemplateField,
)


class ValuesError(Exception):
    def __init__(self, errors: dict[str, str]):
        super().__init__(errors)
        self.errors = errors


def is_visible(field: TemplateField, values: dict[str, Any]) -> bool:
    if not field.visible_if:
        return True
    return all(values.get(ref) in allowed for ref, allowed in field.visible_if.items())


def dependency_params(field: SelectField, values: dict[str, Any]) -> dict[str, Any]:
    return {ref: values.get(ref) for ref in field.depends_on}


async def validate_values(
    template: Template,
    raw: dict[str, Any],
    providers: OptionsProviders,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return clean values (defaults applied, hidden fields dropped) or raise ValuesError.

    `previous`: the server's current values when editing. Hidden secrets keep their value instead of
    being generated again, and unchanged dynamic selects aren't re-checked against a provider (so
    changing the memory doesn't need Mojang to be reachable).
    """
    previous = previous or {}
    errors: dict[str, str] = {}
    known = {f.id for f in template.fields}
    for key in raw.keys() - known:
        errors[key] = "unknown field"

    clean: dict[str, Any] = {}
    # Fields are walked in order, so visible_if / depends_on only ever look at validated values.
    for field in template.fields:
        if not is_visible(field, clean):
            continue

        value = raw.get(field.id)
        if isinstance(field, SecretField) and field.hidden:
            value = previous.get(field.id)  # never user-supplied
        if value is None or value == "":
            value = getattr(field, "default", None)

        if value is None:
            if isinstance(field, SecretField) and field.generate:
                clean[field.id] = secrets.token_urlsafe(field.length)[: field.length]
            elif field.required:
                errors[field.id] = "required"
            continue

        unchanged_dynamic = (
            isinstance(field, SelectField) and field.options_from and previous.get(field.id) == value
        )
        error = None if unchanged_dynamic else await _check(field, value, clean, providers)
        if error:
            errors[field.id] = error
        else:
            clean[field.id] = value

    if errors:
        raise ValuesError(errors)
    return clean


async def _check(
    field: TemplateField, value: Any, clean: dict[str, Any], providers: OptionsProviders
) -> str | None:
    match field:
        case StringField() | SecretField():
            if not isinstance(value, str):
                return "must be a string"
            if isinstance(field, StringField):
                if not field.min_length <= len(value) <= field.max_length:
                    return f"length must be between {field.min_length} and {field.max_length}"
                if field.pattern and not re.fullmatch(field.pattern, value):
                    return "invalid format"

        case NumberField():
            # bool is a subclass of int in Python; don't accept `true` as 1.
            if not isinstance(value, int) or isinstance(value, bool):
                return "must be an integer"
            if field.min is not None and value < field.min:
                return f"must be at least {field.min}"
            if field.max is not None and value > field.max:
                return f"must be at most {field.max}"

        case BooleanField():
            if not isinstance(value, bool):
                return "must be true or false"
            if field.must_be is not None and value != field.must_be:
                return "must be accepted" if field.must_be else "must be declined"

        case SelectField():
            if not isinstance(value, str):
                return "must be a string"
            if field.options_from:
                try:
                    options = await providers.get(field.options_from, dependency_params(field, clean))
                except InvalidParams:
                    return "not one of the available options"
                except ProviderError:
                    return "could not load the list of options, try again later"
            else:
                options = field.options
            if value not in {o.value for o in options}:
                return "not one of the available options"

    return None


def public_values(template: Template | None, values: dict[str, Any]) -> dict[str, Any]:
    """Strip secrets before sending values to the browser."""
    if template is None:
        return {}
    secret_ids = {f.id for f in template.fields if isinstance(f, SecretField)}
    return {k: v for k, v in values.items() if k not in secret_ids}
