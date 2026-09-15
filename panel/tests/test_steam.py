import json
import os
import time

import httpx
import pytest

from app.games import steam as steam_module
from app.games.steam import SteamAssets

ASSETS = {
    "asset_url_format": "steam/apps/10/${FILENAME}?t=1",
    "header": "header.jpg",
    "community_icon": "abc123",
}


class FakeSteam:
    def __init__(self):
        self.requests: list[str] = []
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if self.down:
            return httpx.Response(503)
        if request.url.path.endswith("GetItems/v1/"):
            query = json.loads(request.url.params["input_json"])
            assert query["ids"] == [{"appid": 10}]
            return httpx.Response(200, json={"response": {"store_items": [{"assets": ASSETS}]}})
        if "not-an-image" in str(request.url):
            return httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
        return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})


@pytest.fixture
def fake():
    return FakeSteam()


@pytest.fixture
def steam(fake, tmp_path):
    return SteamAssets(httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)), tmp_path)


async def test_downloads_and_caches(steam, fake):
    icon = await steam.get(10, "icon")
    header = await steam.get(10, "header")
    assert icon.read_bytes() == b"\xff\xd8jpeg" and header.name == "header.jpg"
    assert "images/apps/10/abc123.jpg" in fake.requests[1]
    assert fake.requests[3] == "https://shared.steamstatic.com/store_item_assets/steam/apps/10/header.jpg?t=1"

    count = len(fake.requests)
    assert await steam.get(10, "icon") == icon
    assert len(fake.requests) == count  # served from disk


async def test_failure_is_remembered_and_stale_copy_is_kept(steam, fake):
    path = await steam.get(10, "icon")
    old = time.time() - steam_module.REFRESH_AFTER - 10
    os.utime(path, (old, old))  # expired

    fake.down = True
    assert await steam.get(10, "icon") == path  # stale beats nothing
    count = len(fake.requests)
    assert await steam.get(10, "icon") == path
    assert len(fake.requests) == count  # no retry within the hour


async def test_unknown_app_returns_none(steam, fake):
    fake.down = True
    assert await steam.get(10, "header") is None


async def test_rejects_non_images(steam, monkeypatch):
    monkeypatch.setattr(steam_module, "ICON_URL", "https://cdn.example/not-an-image/{appid}/{hash}")
    assert await steam.get(10, "icon") is None
