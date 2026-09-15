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


FORGE_MAVEN = "https://files.minecraftforge.net/net/minecraftforge/forge"
NEOFORGE_VERSIONS = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.json()


async def _minecraft_releases(client: httpx.AsyncClient) -> list[str]:
    """Release ids, newest first."""
    manifest = await _get_json(client, MOJANG_MANIFEST)
    return [v["id"] for v in manifest["versions"] if v["type"] == "release"]


def neoforge_minecraft_version(neoforge: str) -> str | None:
    """The Minecraft version a NeoForge build is for: 21.1.77 → 1.21.1, 21.0.3 → 1.21, 26.1.0.19 → 26.1."""
    parts = neoforge.split("-")[0].split(".")
    if not all(p.isdigit() for p in parts):
        return None  # April Fools' builds like 0.25w14craftmine.3
    if len(parts) == 3:  # before year-based Minecraft versions
        major, minor = parts[0], parts[1]
        return f"1.{major}" if minor == "0" else f"1.{major}.{minor}"
    if len(parts) == 4:
        year, drop, patch = parts[0], parts[1], parts[2]
        return f"{year}.{drop}" if patch == "0" else f"{year}.{drop}.{patch}"
    return None


async def minecraft_versions(client: httpx.AsyncClient, params: dict[str, Any]) -> list[Option]:
    loader = params.get("loader")
    if loader == "fabric":
        ids = [
            v["version"] for v in await _get_json(client, f"{FABRIC_META}/versions/game") if v.get("stable")
        ]
    elif loader == "forge":
        # Forge's list also has pre-releases and snapshots: keep releases, in Mojang's order.
        forge = await _get_json(client, f"{FORGE_MAVEN}/maven-metadata.json")
        ids = [i for i in await _minecraft_releases(client) if i in forge]
    elif loader == "neoforge":
        builds = (await _get_json(client, NEOFORGE_VERSIONS))["versions"]
        supported = {neoforge_minecraft_version(b) for b in builds}
        ids = [i for i in await _minecraft_releases(client) if i in supported]
    else:
        # TODO: Paper lags behind new releases; ask the Paper API for its own list.
        ids = await _minecraft_releases(client)
    return [Option(value=i, label=i) for i in ids]


async def minecraft_loader_versions(client: httpx.AsyncClient, params: dict[str, Any]) -> list[Option]:
    """Loader builds for one Minecraft version, the one to pick by default first."""
    version = params.get("version")
    loader = params.get("loader")
    if not version:
        return []

    if loader == "fabric":
        options = []
        for entry in await _get_json(client, f"{FABRIC_META}/versions/loader/{version}"):
            build = entry["loader"]
            label = build["version"] if build.get("stable") else f"{build['version']} (unstable)"
            options.append(Option(value=build["version"], label=label))
        return options

    if loader == "forge":
        builds = (await _get_json(client, f"{FORGE_MAVEN}/maven-metadata.json")).get(version, [])
        promos = (await _get_json(client, f"{FORGE_MAVEN}/promotions_slim.json"))["promos"]
        recommended = promos.get(f"{version}-recommended")
        latest = promos.get(f"{version}-latest")
        # "1.20.1-47.3.0" → "47.3.0"; old ones also carry the version at the end: "1.7.10-10.13.4.1614-1.7.10"
        ids = [b.removeprefix(f"{version}-").removesuffix(f"-{version}") for b in reversed(builds)]
        if recommended in ids:
            ids.insert(0, ids.pop(ids.index(recommended)))
        tags = {recommended: "recommended", latest: "latest"}
        return [Option(value=i, label=f"{i} ({tags[i]})" if i in tags else i) for i in ids]

    if loader == "neoforge":
        builds = [
            b
            for b in (await _get_json(client, NEOFORGE_VERSIONS))["versions"]
            if neoforge_minecraft_version(b) == version
        ]

        # Newest first; betas and alphas ("21.0.0-beta", "26.1.0.0-alpha.9+snapshot-6") after stable builds.
        def order(build: str) -> tuple:
            base, _, suffix = build.partition("-")
            return (suffix == "", [int(p) for p in base.split(".")])

        builds.sort(key=order, reverse=True)
        return [
            Option(value=b, label=b if "-" not in b else f"{b} ({b.partition('-')[2].split('.')[0]})")
            for b in builds
        ]

    return []


def default_providers(client: httpx.AsyncClient, ttl: int) -> OptionsProviders:
    providers = OptionsProviders(client, ttl)
    providers.register("minecraft.versions", minecraft_versions)
    providers.register("minecraft.loader_versions", minecraft_loader_versions)
    return providers
