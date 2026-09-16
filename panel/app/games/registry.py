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
    """The template's icon file, only if it exists and lies inside the template's own folder."""
    if not template.icon:
        return None
    root = (template.directory or directory).resolve()
    path = (root / template.icon).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def load_template_file(path: Path, providers: OptionsProviders) -> Template:
    """One template file, fully checked. Raises TemplateLoadError with what's wrong."""
    try:
        template = Template.model_validate(yaml.safe_load(path.read_text()))
    except (yaml.YAMLError, ValidationError, OSError) as exc:
        raise TemplateLoadError(f"{path.name}: {exc}") from exc
    template._directory = path.parent

    for field in template.fields:
        if isinstance(field, SelectField) and field.options_from and field.options_from not in providers:
            raise TemplateLoadError(
                f"{path.name}: field '{field.id}' uses unknown provider '{field.options_from}'"
            )
    if template.icon and icon_path(path.parent, template) is None:
        raise TemplateLoadError(f"{path.name}: icon '{template.icon}' not found in {path.parent}")
    if template.install and template.install.script:
        script = (path.parent / template.install.script).resolve()
        if not script.is_relative_to(path.parent.resolve()) or not script.is_file():
            raise TemplateLoadError(
                f"{path.name}: install script '{template.install.script}' not found in {path.parent}"
            )
    return template


def _add(templates: dict[str, Template], template: Template, path: Path) -> None:
    if template.group:
        other = next(
            (
                t
                for t in templates.values()
                if t.group and t.group.id == template.group.id and t.id != template.id
            ),
            None,
        )
        if other and other.group.name != template.group.name:
            raise TemplateLoadError(
                f"{path.name}: group '{template.group.id}' is named '{other.group.name}' in {other.id}"
            )
    templates[template.id] = template


def load_templates(
    directory: Path, providers: OptionsProviders, custom_directory: Path | None = None
) -> dict[str, Template]:
    """The bundled templates, then an administrator's own from `custom_directory`.

    A broken bundled template stops the panel from starting: it's a bug. A broken custom one is skipped
    with an error in the log, so a typo doesn't take the panel down. A custom template with the id of a
    bundled one replaces it.
    """
    templates: dict[str, Template] = {}
    bundled_ids: set[str] = set()
    for path in sorted(directory.glob("*.yaml")):
        template = load_template_file(path, providers)
        if template.id in templates:
            raise TemplateLoadError(f"{path.name}: duplicate template id '{template.id}'")
        _add(templates, template, path)
        bundled_ids.add(template.id)

    custom = 0
    for path in (
        sorted(custom_directory.glob("*.yaml")) if custom_directory and custom_directory.is_dir() else []
    ):
        try:
            template = load_template_file(path, providers)
            if template.id in templates and template.id not in bundled_ids:
                raise TemplateLoadError(f"{path.name}: duplicate template id '{template.id}'")
            if template.id in bundled_ids:
                log.info("%s replaces the bundled template '%s'", path, template.id)
                bundled_ids.discard(template.id)
            _add(templates, template, path)
            custom += 1
        except TemplateLoadError as exc:
            log.error("skipping custom template %s", exc)

    log.info("Loaded %d game template(s) from %s, %d of them custom", len(templates), directory, custom)
    return templates


def check_templates(directory: Path, providers: OptionsProviders) -> list[tuple[Path, str | None]]:
    """Every template file in a folder with its problem, or None if it loads."""
    results = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            load_template_file(path, providers)
            results.append((path, None))
        except TemplateLoadError as exc:
            results.append((path, str(exc)))
    return results
