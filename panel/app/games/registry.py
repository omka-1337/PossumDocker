import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.games.providers import OptionsProviders
from app.games.schema import SelectField, Template

log = logging.getLogger(__name__)


class TemplateLoadError(Exception):
    pass


def icon_path(directory: Path, template: Template) -> Path | None:
    """The template's icon file, only if it exists and lies inside the templates directory."""
    if not template.icon:
        return None
    root = directory.resolve()
    path = (root / template.icon).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def load_templates(directory: Path, providers: OptionsProviders) -> dict[str, Template]:
    """Load and validate every templates/*.yaml. Any broken template stops the panel from starting."""
    templates: dict[str, Template] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            template = Template.model_validate(yaml.safe_load(path.read_text()))
        except (yaml.YAMLError, ValidationError) as exc:
            raise TemplateLoadError(f"{path.name}: {exc}") from exc

        if template.id in templates:
            raise TemplateLoadError(f"{path.name}: duplicate template id '{template.id}'")
        for field in template.fields:
            if isinstance(field, SelectField) and field.options_from and field.options_from not in providers:
                raise TemplateLoadError(
                    f"{path.name}: field '{field.id}' uses unknown provider '{field.options_from}'"
                )
        if template.icon and icon_path(directory, template) is None:
            raise TemplateLoadError(f"{path.name}: icon '{template.icon}' not found in {directory}")
        if template.group:
            other = next((t for t in templates.values() if t.group and t.group.id == template.group.id), None)
            if other and other.group.name != template.group.name:
                raise TemplateLoadError(
                    f"{path.name}: group '{template.group.id}' is named '{other.group.name}' in {other.id}"
                )
        templates[template.id] = template

    log.info("Loaded %d game template(s) from %s", len(templates), directory)
    return templates
