"""Options providers: code that fetches select options at runtime (e.g. game versions)."""

import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.games.schema import Option

ProviderFn = Callable[[httpx.AsyncClient, dict[str, Any]], Awaitable[list[Option]]]


class ProviderError(Exception):
    pass


class OptionsProviders:
    def __init__(self, client: httpx.AsyncClient, ttl: int = 3600):
        self._client = client
        self._ttl = ttl
        self._providers: dict[str, ProviderFn] = {}
        self._cache: dict[tuple, tuple[float, list[Option]]] = {}

    def register(self, name: str, fn: ProviderFn) -> None:
        self._providers[name] = fn

    def __contains__(self, name: str) -> bool:
        return name in self._providers

    async def get(self, name: str, params: dict[str, Any]) -> list[Option]:
        if name not in self._providers:
            raise ProviderError(f"unknown options provider '{name}'")

        key = (name, tuple(sorted(params.items())))
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return cached[1]

        try:
            options = await self._providers[name](self._client, params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider '{name}' failed: {exc}") from exc

        self._cache[key] = (time.monotonic(), options)
        return options


# --- Minecraft ---------------------------------------------------------------

MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
FABRIC_META = "https://meta.fabricmc.net/v2"


async def minecraft_versions(client: httpx.AsyncClient, params: dict[str, Any]) -> list[Option]:
    if params.get("loader") == "fabric":
        resp = await client.get(f"{FABRIC_META}/versions/game")
        resp.raise_for_status()
        ids = [v["version"] for v in resp.json() if v.get("stable")]
    else:
        # TODO: Paper lags behind new releases; ask the Paper API for its own list.
        resp = await client.get(MOJANG_MANIFEST)
        resp.raise_for_status()
        ids = [v["id"] for v in resp.json()["versions"] if v["type"] == "release"]
    return [Option(value=i, label=i) for i in ids]


async def minecraft_loader_versions(
    client: httpx.AsyncClient, params: dict[str, Any]
) -> list[Option]:
    version = params.get("version")
    if params.get("loader") != "fabric" or not version:
        return []
    resp = await client.get(f"{FABRIC_META}/versions/loader/{version}")
    resp.raise_for_status()
    options = []
    for entry in resp.json():
        loader = entry["loader"]
        label = loader["version"] if loader.get("stable") else f"{loader['version']} (unstable)"
        options.append(Option(value=loader["version"], label=label))
    return options


def default_providers(client: httpx.AsyncClient, ttl: int) -> OptionsProviders:
    providers = OptionsProviders(client, ttl)
    providers.register("minecraft.versions", minecraft_versions)
    providers.register("minecraft.loader_versions", minecraft_loader_versions)
    return providers
