"""Read and edit game config files without losing comments, order or unknown keys."""

import re
from dataclasses import dataclass
from typing import Literal

from app.games.schema import ConfigFile, ConfigHint


@dataclass
class _Line:
    raw: str
    key: str | None = None
    value: str | None = None


class ConfigDocument:
    """A parsed config file. Only lines whose value changed are re-rendered."""

    def __init__(self, fmt: Literal["properties", "cvars"], data: bytes):
        self.format = fmt
        # Older Java wrote properties as Latin-1; keep whatever the file uses.
        try:
            text, self._encoding = data.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            text, self._encoding = data.decode("latin-1"), "latin-1"
        self._lines = [self._parse_line(raw) for raw in text.splitlines()]

    def items(self) -> dict[str, str]:
        # For a key set twice the game uses the last one, so do we.
        return {line.key: line.value for line in self._lines if line.key is not None}

    def set(self, key: str, value: str) -> None:
        matches = [line for line in self._lines if line.key == key]
        if not matches:
            self._lines.append(_Line(raw=self._render_line(key, value), key=key, value=value))
            return
        for line in matches:
            if line.value != value:
                line.value = value
                line.raw = self._render_line(key, value)

    def render(self) -> bytes:
        text = "\n".join(line.raw for line in self._lines) + "\n"
        try:
            return text.encode(self._encoding)
        except UnicodeEncodeError:
            return text.encode("utf-8")

    # --- formats -------------------------------------------------------------

    def _parse_line(self, raw: str) -> _Line:
        if self.format == "properties":
            return _parse_properties_line(raw)
        return _parse_cvar_line(raw)

    def _render_line(self, key: str, value: str) -> str:
        if self.format == "properties":
            return f"{_escape_properties(key, is_key=True)}={_escape_properties(value)}"
        return f'{key} "{value}"'


# Java .properties (Minecraft) -------------------------------------------------


def _parse_properties_line(raw: str) -> _Line:
    stripped = raw.lstrip()
    if not stripped or stripped[0] in "#!":
        return _Line(raw)
    # The key ends at the first unescaped '=', ':' or whitespace.
    i = 0
    while i < len(stripped):
        ch = stripped[i]
        if ch == "\\":
            i += 2
            continue
        if ch in "=: \t":
            break
        i += 1
    key = stripped[:i]
    rest = stripped[i:].lstrip(" \t")
    if rest[:1] in ("=", ":"):
        rest = rest[1:].lstrip(" \t")
    return _Line(raw, key=_unescape_properties(key), value=_unescape_properties(rest))


def _unescape_properties(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch != "\\" or i + 1 == len(text):
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1]
        if nxt == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", text[i + 2 : i + 6]):
            out.append(chr(int(text[i + 2 : i + 6], 16)))
            i += 6
            continue
        out.append({"t": "\t", "n": "\n", "r": "\r", "f": "\f"}.get(nxt, nxt))
        i += 2
    # Join surrogate pairs (😀) back into single characters.
    return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16")


def _escape_properties(text: str, is_key: bool = False) -> str:
    out = []
    for i, ch in enumerate(text):
        if ch == "\\":
            out.append("\\\\")
        elif ch in "\t\n\r\f":
            out.append({"\t": "\\t", "\n": "\\n", "\r": "\\r", "\f": "\\f"}[ch])
        elif ch in "=:#!" or (ch == " " and (is_key or i == 0)):
            out.append("\\" + ch)
        elif ord(ch) > 0x7E:
            # Java reads \uXXXX whatever encoding the file is in; emoji take two UTF-16 units.
            data = ch.encode("utf-16-be")
            out.extend(f"\\u{int.from_bytes(data[j : j + 2], 'big'):04x}" for j in range(0, len(data), 2))
        else:
            out.append(ch)
    return "".join(out)


# GoldSrc/Source cvars (server.cfg) ---------------------------------------------

_CVAR = re.compile(r'^\s*([A-Za-z_][\w.]*)\s+(?:"([^"]*)"|(\S+))\s*(?://.*)?$')


def _parse_cvar_line(raw: str) -> _Line:
    match = _CVAR.match(raw)
    if not match or raw.lstrip().startswith("//"):
        return _Line(raw)
    key, quoted, bare = match.groups()
    return _Line(raw, key=key, value=quoted if quoted is not None else bare)


# Validation --------------------------------------------------------------------


class ConfigValuesError(Exception):
    def __init__(self, errors: dict[str, str]):
        super().__init__(errors)
        self.errors = errors


def validate_config_values(config: ConfigFile, values: dict[str, str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for key, value in values.items():
        if key in config.managed:
            errors[key] = "managed by the panel"
        elif not isinstance(value, str):
            errors[key] = "must be a string"
        elif error := _check_hint(config.hints.get(key), value, config.format):
            errors[key] = error
    if errors:
        raise ConfigValuesError(errors)
    return values


def _check_hint(hint: ConfigHint | None, value: str, fmt: str) -> str | None:
    if "\n" in value or "\r" in value:
        return "must be a single line"
    if fmt == "cvars" and '"' in value:
        return 'cannot contain "'
    if len(value) > 1000:
        return "too long"
    if hint is None:
        return None
    match hint.type:
        case "number":
            if not re.fullmatch(r"-?\d+", value):
                return "must be an integer"
            if hint.min is not None and int(value) < hint.min:
                return f"must be at least {hint.min}"
            if hint.max is not None and int(value) > hint.max:
                return f"must be at most {hint.max}"
        case "boolean":
            if value not in (hint.true_value, hint.false_value):
                return f"must be {hint.true_value} or {hint.false_value}"
        case "select":
            if value not in hint.options:
                return "not one of the available options"
    return None
