import json
import os
import time

import httpx
import pytest

from app.games import art as art_module
from app.games.art import ArtCache

ASSETS = {
    "asset_url_format": "steam/apps/10/${FILENAME}?t=1",
    "header": "header.jpg",
    "community_icon": "abc123",
}
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"/>'


class FakeWeb:
    def __init__(self):
        self.requests: list[str] = []
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if self.down:
            return httpx.Response(503)
        if request.url.path.endswith("GetItems/v1/"):
            assert json.loads(request.url.params["input_json"])["ids"] == [{"appid": 10}]
            return httpx.Response(200, json={"response": {"store_items": [{"assets": ASSETS}]}})
        if url.endswith(".svg"):
            return httpx.Response(200, content=SVG, headers={"content-type": "image/svg+xml; charset=utf-8"})
        if "page" in url:
            return httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
        return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})


@pytest.fixture
def web():
    return FakeWeb()


@pytest.fixture
def art(web, tmp_path):
    return ArtCache(httpx.AsyncClient(transport=httpx.MockTransport(web.handler)), tmp_path)


async def test_steam_art_is_downloaded_and_cached(art, web):
    icon = await art.from_steam(10, "icon")
    cover = await art.from_steam(10, "cover")
    assert icon.read_bytes() == b"\xff\xd8jpeg" and cover.suffix == ".jpg"
    assert "images/apps/10/abc123.jpg" in web.requests[1]
    assert web.requests[3] == "https://shared.steamstatic.com/store_item_assets/steam/apps/10/header.jpg?t=1"

    count = len(web.requests)
    assert await art.from_steam(10, "icon") == icon
    assert len(web.requests) == count  # served from disk


async def test_url_art_keeps_its_format(art):
    path = await art.from_url("https://cdn.example/icons/minecraft.svg")
    assert path.suffix == ".svg" and path.read_bytes() == SVG
    # Another link is another image.
    assert await art.from_url("https://cdn.example/icons/other.svg") != path


async def test_failure_is_remembered_and_stale_copy_is_kept(art, web):
    path = await art.from_steam(10, "icon")
    old = time.time() - art_module.REFRESH_AFTER - 10
    os.utime(path, (old, old))  # expired

    web.down = True
    assert await art.from_steam(10, "icon") == path  # stale beats nothing
    count = len(web.requests)
    assert await art.from_steam(10, "icon") == path
    assert len(web.requests) == count  # no retry within the hour


async def test_nothing_to_serve(art, web):
    assert await art.from_url("https://cdn.example/page") is None  # not an image
    assert await art.from_url("http://cdn.example/plain.jpg") is None  # not https
    web.down = True
    assert await art.from_steam(10, "cover") is None
