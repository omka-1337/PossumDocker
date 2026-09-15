"""Game art from Steam, fetched at runtime and cached on disk.

The images belong to their publishers and are not part of this repository: the panel downloads
them from Valve's CDN on first use, the way a browser would, and serves the cached copy after that.
Browsers never talk to Steam, and the art keeps working offline once cached.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Literal

import httpx

log = logging.getLogger(__name__)

AssetKind = Literal["icon", "header"]

ITEMS_API = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
ICON_URL = "https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/apps/{appid}/{hash}.jpg"
STORE_ASSETS = "https://shared.steamstatic.com/store_item_assets/"

REFRESH_AFTER = 30 * 24 * 3600  # art rarely changes
RETRY_AFTER_FAILURE = 3600  # don't hammer Steam when it's down or the app doesn't exist
MAX_IMAGE_BYTES = 2 * 1024 * 1024


class SteamAssets:
    def __init__(self, client: httpx.AsyncClient, cache_dir: Path):
        self._client = client
        self._dir = cache_dir / "steam"
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}
        self._failed: dict[tuple[int, str], float] = {}

    def _file(self, appid: int, kind: AssetKind) -> Path:
        return self._dir / str(appid) / f"{kind}.jpg"

    async def get(self, appid: int, kind: AssetKind) -> Path | None:
        """Path to the cached image, downloading it if needed; None if Steam has none for this app."""
        path = self._file(appid, kind)
        if path.exists() and time.time() - path.stat().st_mtime < REFRESH_AFTER:
            return path

        key = (appid, kind)
        if time.monotonic() - self._failed.get(key, -RETRY_AFTER_FAILURE) < RETRY_AFTER_FAILURE:
            return path if path.exists() else None

        async with self._locks.setdefault(key, asyncio.Lock()):
            if path.exists() and time.time() - path.stat().st_mtime < REFRESH_AFTER:
                return path  # another request just fetched it
            try:
                await self._download(appid, kind, path)
                self._failed.pop(key, None)
            except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
                log.warning("steam %s for app %s: %s", kind, appid, exc)
                self._failed[key] = time.monotonic()
        # A stale copy beats no picture.
        return path if path.exists() else None

    async def _download(self, appid: int, kind: AssetKind, path: Path) -> None:
        url = await self._asset_url(appid, kind)
        async with self._client.stream("GET", url) as resp:
            resp.raise_for_status()
            if not resp.headers.get("content-type", "").startswith("image/"):
                raise ValueError(f"not an image: {resp.headers.get('content-type')}")
            data = bytearray()
            async for chunk in resp.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError("image too large")

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic: a half-written file is never served

    async def _asset_url(self, appid: int, kind: AssetKind) -> str:
        query = {
            "ids": [{"appid": appid}],
            "context": {"language": "english", "country_code": "US"},
            "data_request": {"include_assets": True},
        }
        resp = await self._client.get(ITEMS_API, params={"input_json": json.dumps(query)})
        resp.raise_for_status()
        assets = resp.json()["response"]["store_items"][0]["assets"]
        if kind == "icon":
            return ICON_URL.format(appid=appid, hash=assets["community_icon"])
        # e.g. "steam/apps/10/${FILENAME}?t=1745368572"
        return STORE_ASSETS + assets["asset_url_format"].replace("${FILENAME}", assets["header"])
