"""Game art fetched at runtime from public URLs (Steam, or any https link in a template) and cached on disk.

The images belong to their owners and are not part of this repository: the panel downloads them on
first use, the way a browser would, and serves the cached copy after that. Browsers only talk to the
panel, and the art keeps working offline once cached.
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import httpx

log = logging.getLogger(__name__)

ArtKind = Literal["icon", "cover"]

STEAM_ITEMS_API = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
STEAM_ICON_URL = "https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/apps/{appid}/{hash}.jpg"
STEAM_STORE_ASSETS = "https://shared.steamstatic.com/store_item_assets/"

REFRESH_AFTER = 30 * 24 * 3600  # art rarely changes
AVATAR_REFRESH_AFTER = 24 * 3600  # players change theirs now and then
STEAM_PROFILE_XML = "https://steamcommunity.com/profiles/{steam_id}/?xml=1"
STEAM_AVATAR_HOSTS = re.compile(r"^https://(avatars(\.[a-z]+)?\.steamstatic\.com|steamcdn-a\.akamaihd\.net)/")
MOJANG_PROFILE = "https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"
MINECRAFT_TEXTURES = re.compile(r"^https?://textures\.minecraft\.net/texture/[0-9a-f]+$")
# SteamID64 of STEAM_0:0:0; STEAM_X:Y:Z is this + Z * 2 + Y.
STEAM_ID64_BASE = 76561197960265728
RETRY_AFTER_FAILURE = 3600  # don't hammer a host that is down or a link that is gone
MAX_IMAGE_BYTES = 2 * 1024 * 1024
EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
}
MEDIA_TYPES = {ext: media for media, ext in EXTENSIONS.items()}


class ArtCache:
    def __init__(self, client: httpx.AsyncClient, cache_dir: Path):
        self._client = client
        self._dir = cache_dir / "art"
        self._locks: dict[str, asyncio.Lock] = {}
        self._failed: dict[str, float] = {}

    async def from_url(self, url: str) -> Path | None:
        # Keyed by the URL: point the template to another image and it's fetched again.
        key = "url-" + hashlib.sha256(url.encode()).hexdigest()[:24]
        return await self._get(key, lambda: _const(url))

    async def from_steam(self, appid: int, kind: ArtKind) -> Path | None:
        return await self._get(f"steam-{appid}-{kind}", lambda: self._steam_url(appid, kind))

    # --- players -------------------------------------------------------------

    async def steam_avatar(self, steam_id: str) -> Path | None:
        """A Steam user's avatar, from their public profile (no API key needed)."""
        steam_id64 = to_steam_id64(steam_id)
        if steam_id64 is None:
            return None
        return await self._get(
            f"steam-avatar-{steam_id64}", lambda: self._steam_avatar_url(steam_id64), AVATAR_REFRESH_AFTER
        )

    async def minecraft_skin(self, uuid: str) -> Path | None:
        """A Minecraft player's skin; the UI shows its face. Offline-mode players have none."""
        uuid = uuid.replace("-", "").lower()
        # Version 3 UUIDs are made up by servers in offline mode: Mojang doesn't know them.
        if not re.fullmatch(r"[0-9a-f]{32}", uuid) or uuid[12] != "4":
            return None
        return await self._get(
            f"minecraft-skin-{uuid}", lambda: self._minecraft_skin_url(uuid), AVATAR_REFRESH_AFTER
        )

    async def _steam_avatar_url(self, steam_id64: int) -> str:
        resp = await self._client.get(STEAM_PROFILE_XML.format(steam_id=steam_id64))
        resp.raise_for_status()
        match = re.search(r"<avatarFull><!\[CDATA\[(.*?)\]\]></avatarFull>", resp.text)
        if not match or not STEAM_AVATAR_HOSTS.match(match.group(1)):
            raise ValueError("no avatar in the profile")
        return match.group(1)

    async def _minecraft_skin_url(self, uuid: str) -> str:
        resp = await self._client.get(MOJANG_PROFILE.format(uuid=uuid))
        resp.raise_for_status()
        if resp.status_code == 204:
            raise ValueError("no such player")
        properties = {p["name"]: p["value"] for p in resp.json().get("properties", [])}
        textures = json.loads(base64.b64decode(properties["textures"]))
        url = textures["textures"]["SKIN"]["url"]
        if not MINECRAFT_TEXTURES.match(url):
            raise ValueError(f"unexpected skin address {url}")
        return "https://" + url.split("://", 1)[1]

    # --- cache ---------------------------------------------------------------

    def _cached(self, key: str) -> Path | None:
        return next((p for p in self._dir.glob(f"{key}.*") if p.suffix != ".part"), None)

    async def _get(
        self, key: str, resolve: Callable[[], Awaitable[str]], refresh_after: int = REFRESH_AFTER
    ) -> Path | None:
        """Path to the cached image, downloading it if needed; None if there's no image to be had."""
        cached = self._cached(key)
        if cached and time.time() - cached.stat().st_mtime < refresh_after:
            return cached
        if time.monotonic() - self._failed.get(key, -RETRY_AFTER_FAILURE) < RETRY_AFTER_FAILURE:
            return cached

        async with self._locks.setdefault(key, asyncio.Lock()):
            cached = self._cached(key)
            if cached and time.time() - cached.stat().st_mtime < refresh_after:
                return cached  # another request just fetched it
            try:
                cached = await self._download(key, await resolve(), cached)
                self._failed.pop(key, None)
            except (httpx.HTTPError, ValueError, KeyError, IndexError, OSError) as exc:
                log.warning("art %s: %s", key, exc)
                self._failed[key] = time.monotonic()
        # A stale copy beats no picture.
        return cached

    async def _download(self, key: str, url: str, old: Path | None) -> Path:
        if not url.startswith("https://"):
            raise ValueError(f"only https URLs are fetched: {url}")
        async with self._client.stream("GET", url) as resp:
            resp.raise_for_status()
            media_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if media_type not in EXTENSIONS:
                raise ValueError(f"not a supported image type: {media_type or 'unknown'}")
            data = bytearray()
            async for chunk in resp.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError("image too large")

        return await asyncio.to_thread(self._store, key, EXTENSIONS[media_type], bytes(data), old)

    def _store(self, key: str, extension: str, data: bytes, old: Path | None) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{key}.{extension}"
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic: a half-written file is never served
        if old and old != path:
            old.unlink(missing_ok=True)  # the image changed format
        return path

    # --- Steam ---------------------------------------------------------------

    async def _steam_url(self, appid: int, kind: ArtKind) -> str:
        query = {
            "ids": [{"appid": appid}],
            "context": {"language": "english", "country_code": "US"},
            "data_request": {"include_assets": True},
        }
        resp = await self._client.get(STEAM_ITEMS_API, params={"input_json": json.dumps(query)})
        resp.raise_for_status()
        assets = resp.json()["response"]["store_items"][0]["assets"]
        if kind == "icon":
            return STEAM_ICON_URL.format(appid=appid, hash=assets["community_icon"])
        # e.g. "steam/apps/10/${FILENAME}?t=1745368572"
        return STEAM_STORE_ASSETS + assets["asset_url_format"].replace("${FILENAME}", assets["header"])


def to_steam_id64(steam_id: str) -> int | None:
    """ "STEAM_0:1:12345" (GoldSrc, Source) or a SteamID64 as a number; None for LAN and bot ids."""
    if match := re.fullmatch(r"STEAM_[0-5]:([01]):(\d+)", steam_id):
        return STEAM_ID64_BASE + int(match.group(2)) * 2 + int(match.group(1))
    if re.fullmatch(r"7656119\d{10}", steam_id):
        return int(steam_id)
    return None


async def _const(value: str) -> str:
    return value
