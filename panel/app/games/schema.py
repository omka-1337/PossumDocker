"""Pydantic models describing a game template (templates/*.yaml)."""

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]


class StrictModel(BaseModel):
    # A typo in a template key should fail loudly instead of being silently ignored.
    model_config = ConfigDict(extra="forbid")


class Option(StrictModel):
    value: str
    label: str


class BaseField(StrictModel):
    id: Identifier
    label: str
    help: str | None = None
    help_url: str | None = None
    required: bool = False
    # Shown only when every listed field has one of the listed values:
    #   visible_if: {loader: [fabric, forge]}
    visible_if: dict[str, list[Any]] | None = None
    # Can the value be changed after the server is created, and what that costs.
    editable: bool = False
    on_change: Literal["none", "restart", "reinstall"] = "none"


class StringField(BaseField):
    type: Literal["string"]
    default: str | None = None
    min_length: int = 0
    max_length: int = 256
    pattern: str | None = None


class NumberField(BaseField):
    type: Literal["number"]
    default: int | None = None
    min: int | None = None
    max: int | None = None


class BooleanField(BaseField):
    type: Literal["boolean"]
    default: bool = False
    # e.g. an EULA checkbox: the server can't be created unless it is ticked.
    must_be: bool | None = None


class SelectField(BaseField):
    type: Literal["select"]
    default: str | None = None
    options: list[Option] = []
    # Name of a registered options provider, for lists fetched at runtime (game versions).
    options_from: str | None = None
    # Values of these fields are passed to the provider; must be declared earlier.
    depends_on: list[str] = []

    @field_validator("options", mode="before")
    @classmethod
    def _expand_shorthand(cls, value: Any) -> Any:
        # Allow `options: [de_dust2, de_nuke]` as shorthand for value == label.
        if isinstance(value, list):
            return [{"value": v, "label": v} if isinstance(v, str) else v for v in value]
        return value

    @model_validator(mode="after")
    def _one_source(self) -> "SelectField":
        if bool(self.options) == bool(self.options_from):
            raise ValueError(f"select '{self.id}' needs exactly one of 'options' or 'options_from'")
        if self.depends_on and not self.options_from:
            raise ValueError(f"select '{self.id}': 'depends_on' only makes sense with options_from")
        return self


class SecretField(BaseField):
    type: Literal["secret"]
    generate: bool = False
    length: int = Field(default=24, ge=8, le=128)
    # Hidden secrets are never shown in the create form (e.g. an auto-generated RCON password).
    hidden: bool = False


TemplateField = Annotated[
    StringField | NumberField | BooleanField | SelectField | SecretField,
    Field(discriminator="type"),
]


class Port(StrictModel):
    name: Identifier
    container: int = Field(ge=1, le=65535)
    protocol: Literal["tcp", "udp"]
    default_host: int = Field(ge=1, le=65535)


class StopSpec(StrictModel):
    # Console command for a graceful stop; falls back to SIGTERM when unset.
    command: str | None = None
    timeout: int = 30


# Every string below is a Jinja template rendered with the field values, e.g. "{{ version }}".


class RuntimeSpec(StrictModel):
    image: str
    entrypoint: list[str] | None = None
    command: list[str] | None = None
    env: dict[str, str] = {}
    stop: StopSpec = StopSpec()
    # Where the server's data volume is mounted.
    data_path: str = "/data"


class InstallSpec(StrictModel):
    """A one-off container that prepares the data volume, then exits."""

    image: str
    # Shell script, path relative to the templates directory. Runs with `sh`.
    script: str | None = None
    entrypoint: list[str] | None = None
    command: list[str] | None = None
    # Merged over runtime.env, so the installer knows the version, loader etc.
    env: dict[str, str] = {}

    @model_validator(mode="after")
    def _script_or_command(self) -> "InstallSpec":
        if self.script and (self.command or self.entrypoint):
            raise ValueError("install: use either 'script' or 'entrypoint'/'command'")
        return self


class HighlightRule(StrictModel):
    # Matched against each console line in the browser (JavaScript regex syntax; keep it simple).
    pattern: str
    color: Literal["red", "yellow", "green", "blue", "magenta", "cyan", "gray"]

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, pattern: str) -> str:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid pattern {pattern!r}: {exc}") from exc
        return pattern


class ConsoleSpec(StrictModel):
    """How the panel's console shows this game's output."""

    # First matching rule colours the whole line. Lines the game already coloured are left alone.
    highlight: list[HighlightRule] = []
    # Lines that continue the previous record (a Java stack trace under an ERROR) and keep its colour.
    continuation: str | None = None

    @field_validator("continuation")
    @classmethod
    def _continuation_compiles(cls, pattern: str | None) -> str | None:
        if pattern is not None:
            HighlightRule._compiles(pattern)
        return pattern


class ConfigHint(StrictModel):
    """How to present one key of a config file. Keys without a hint are shown as plain text."""

    label: str
    type: Literal["string", "number", "boolean", "select"] = "string"
    help: str | None = None
    options: list[str] = []
    min: int | None = None
    max: int | None = None
    # How the game spells booleans: "true"/"false" for Minecraft, "1"/"0" for GoldSrc cvars.
    true_value: str = "true"
    false_value: str = "false"

    @model_validator(mode="after")
    def _options_for_select(self) -> "ConfigHint":
        if (self.type == "select") != bool(self.options):
            raise ValueError(f"hint '{self.label}': 'options' is required for select and only for it")
        return self


class ConfigFile(StrictModel):
    """A config file the panel lets users edit (server.properties, server.cfg...).

    The file itself is the source of truth: whatever keys this game version wrote are shown.
    Hints only make known keys nicer to edit.
    """

    id: Identifier
    label: str
    # Relative to runtime.data_path.
    path: Annotated[str, Field(pattern=r"^[\w./-]+$")]
    format: Literal["properties", "cvars"]
    # Set by the panel itself (ports, RCON password): shown, but not editable.
    managed: list[str] = []
    hints: dict[str, ConfigHint] = {}

    @field_validator("path")
    @classmethod
    def _stay_inside_data_path(cls, path: str) -> str:
        if path.startswith("/") or ".." in path.split("/"):
            raise ValueError("path must be relative and stay inside the data directory")
        return path


RelativePath = Annotated[str, Field(pattern=r"^[\w.-][\w./-]*$", max_length=255)]


class BackupSpec(StrictModel):
    """What a backup of this game contains and how to take it safely while the server runs."""

    # Only these paths (files or folders, relative to the data volume). Empty: everything.
    # Restore replaces exactly these, so game files that aren't backed up stay untouched.
    paths: list[RelativePath] = []
    # Top-level names (globs allowed) left out of a full backup: caches and downloads the server can get
    # back by itself. A restore leaves them in place, so the server still starts without the internet.
    exclude: list[Annotated[str, Field(pattern=r"^[\w.*?\[\]-]+$", max_length=255)]] = []
    # Console commands around the archive while the server runs, e.g. pause world saving.
    before: list[str] = []
    after: list[str] = []
    # Seconds to wait after `before`, so the game finishes writing.
    wait: int = Field(default=5, ge=0, le=120)

    @field_validator("paths", "exclude")
    @classmethod
    def _no_parent(cls, items: list[str]) -> list[str]:
        for item in items:
            if ".." in item.split("/"):
                raise ValueError(f"'{item}' must stay inside the data directory")
        return items


HttpsUrl = Annotated[str, Field(pattern=r"^https://[^\s]+$", max_length=2000)]


class RemoteArt(StrictModel):
    # Square icon: SVG, PNG, JPEG or WebP.
    icon: HttpsUrl | None = None
    # Wide cover for the game picker; cropped to about 2:1.
    cover: HttpsUrl | None = None


class Template(StrictModel):
    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=64)]
    name: str
    description: str | None = None
    # SVG/PNG/WebP relative to the templates directory, e.g. icons/cs16.svg (see icons/README.md).
    icon: Annotated[str | None, Field(pattern=r"^[\w./-]+\.(svg|png|webp)$")] = None
    # Background of the icon tile, e.g. "#de9b35".
    color: Annotated[str | None, Field(pattern=r"^#[0-9a-fA-F]{6}$")] = None
    # For games on Steam: icon and cover art are fetched from Steam at runtime (`icon` stays the fallback).
    steam_appid: Annotated[int | None, Field(gt=0)] = None
    # Art from any public https link, fetched and cached by the panel; wins over Steam's.
    art: RemoteArt = RemoteArt()
    fields: list[TemplateField] = []
    ports: list[Port] = []
    install: InstallSpec | None = None
    runtime: RuntimeSpec
    config_files: list[ConfigFile] = []
    console: ConsoleSpec = ConsoleSpec()
    backup: BackupSpec = BackupSpec()
    query: dict[str, Any] | None = None

    def config_file(self, config_id: str) -> ConfigFile | None:
        return next((c for c in self.config_files if c.id == config_id), None)

    @model_validator(mode="after")
    def _check_references(self) -> "Template":
        seen: set[str] = set()
        for field in self.fields:
            if field.id in seen:
                raise ValueError(f"duplicate field id '{field.id}'")
            refs = list((field.visible_if or {}).keys())
            if isinstance(field, SelectField):
                refs += field.depends_on
            for ref in refs:
                # Only earlier fields: validation walks fields in order.
                if ref not in seen:
                    raise ValueError(f"field '{field.id}' refers to '{ref}', which is not declared before it")
            seen.add(field.id)

        port_names = [p.name for p in self.ports]
        if len(port_names) != len(set(port_names)):
            raise ValueError("duplicate port name")
        config_ids = [c.id for c in self.config_files]
        if len(config_ids) != len(set(config_ids)):
            raise ValueError("duplicate config file id")
        return self

    def field(self, field_id: str) -> TemplateField | None:
        return next((f for f in self.fields if f.id == field_id), None)
