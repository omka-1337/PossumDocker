"""Soft disk and backup limits."""

import io
import zipfile

from app.runtime.manager import format_size
from tests.conftest import add_user, login, wait_for_status
from tests.test_auth import PASSWORD, grant
from tests.test_backups import wait_for_backup
from tests.test_files import upload
from tests.test_servers import create, create_installed

MB = 1024 * 1024


def limited(client, disk_mb=100, backup_mb=None):
    server = create_installed(client, "cs16")
    body = {"disk_limit_mb": disk_mb}
    if backup_mb is not None:
        body["backup_limit_mb"] = backup_mb
    assert client.patch(f"/api/servers/{server['id']}", json=body).status_code == 200
    return server


def test_uploads_past_the_limit_are_refused_before_they_are_read(client):
    server = limited(client)
    assert upload(client, server, "", {"small.txt": b"x" * MB}).status_code == 204
    resp = upload(client, server, "", {"big.bin": b"x" * 100 * MB})
    assert resp.status_code == 507
    assert "not enough space" in resp.json()["detail"]
    names = {e["name"] for e in client.get(f"/api/servers/{server['id']}/files").json()["entries"]}
    assert "big.bin" not in names


def test_zip_that_unpacks_past_the_limit_is_not_extracted(client):
    server = limited(client)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bomb.bin", b"\0" * 150 * MB)  # compresses to almost nothing
    assert upload(client, server, "", {"bomb.zip": buffer.getvalue()}).status_code == 204
    resp = client.post(f"/api/servers/{server['id']}/files/extract", json={"path": "bomb.zip"})
    assert resp.status_code == 507


def test_copy_needs_room_for_the_copy(client):
    server = limited(client)
    upload(client, server, "", {"world/region.bin": b"x" * 60 * MB})
    resp = client.post(
        f"/api/servers/{server['id']}/files/copy", json={"sources": ["world"], "destination": "backup"}
    )
    assert resp.status_code == 507


def test_a_server_over_its_limit_does_not_start_and_says_why(client):
    server = create_installed(client, "cs16")
    upload(client, server, "", {"data.bin": b"x" * 2 * MB})
    url = f"/api/servers/{server['id']}"
    # Below the 100 MB the API allows: the files already there are over it.
    client.portal.call(lambda: _set(client, server["id"], disk_limit_mb=1))
    resp = client.post(f"{url}/start")
    assert resp.status_code == 409 and "over its disk limit" in resp.json()["detail"]
    assert "can't start" in client.get(url).json()["status_message"]
    # Raising the limit clears it.
    client.patch(url, json={"disk_limit_mb": 1000})
    assert client.get(url).json()["status_message"] is None
    assert client.post(f"{url}/start").status_code == 200


async def _set(client, server_id, **fields):
    from app.models import Server

    async with client.app.state.sessionmaker() as session:
        server = await session.get(Server, server_id)
        for key, value in fields.items():
            setattr(server, key, value)
        await session.commit()


def test_a_running_server_that_grows_past_its_limit_is_stopped(client, runtime):
    server = create_installed(client, "cs16")
    url = f"/api/servers/{server['id']}"
    client.post(f"{url}/start")
    upload(client, server, "", {"world.bin": b"x" * 2 * MB})  # as if the game wrote it
    client.portal.call(lambda: _set(client, server["id"], disk_limit_mb=1))

    client.portal.call(client.app.state.manager.stop_servers_over_disk_limit)
    stopped = wait_for_status(client, server["id"], "stopped")
    assert stopped["status"] == "stopped"
    assert stopped["status_message"].startswith("stopped: over its disk limit")


def test_backups_stop_at_their_limit(client):
    import os

    server = create_installed(client, "cs16")
    sid = server["id"]
    upload(client, server, "", {"cstrike/server.cfg": os.urandom(2 * MB)})  # doesn't compress
    client.portal.call(lambda: _set(client, sid, disk_limit_mb=0, backup_limit_mb=1))
    first = client.post(f"/api/servers/{sid}/backups", json={}).json()
    assert wait_for_backup(client, server, first["id"])["status"] == "ready"

    resp = client.post(f"/api/servers/{sid}/backups", json={})
    assert resp.status_code == 507 and "backup space is full" in resp.json()["detail"]
    assert client.get(f"/api/servers/{sid}/backups").json()["limit"] == MB


def test_storage_report(client):
    server = limited(client, disk_mb=200)
    upload(client, server, "", {"a.bin": b"x" * MB})
    storage = client.get(f"/api/servers/{server['id']}/storage").json()
    assert storage["disk_used"] >= MB
    assert storage["disk_limit"] == 200 * MB
    assert storage["backups_limit"] == 400 * MB  # twice the disk by default


def test_only_administrators_set_disk_limits_and_they_can_at_creation(client):
    resp = create(client, "cs16", name="big")
    sid = resp.json()["id"]
    body = {"template_id": "cs16", "name": "small", "values": {}, "disk_limit_mb": 500, "backup_limit_mb": 0}
    created = client.post("/api/servers", json=body).json()
    assert (created["limits"]["disk_mb"], created["limits"]["backups_mb"]) == (500, 0)
    assert client.post("/api/servers", json={**body, "disk_limit_mb": 10}).status_code == 422

    user_id = add_user(client, "player", PASSWORD)
    grant(client, sid, user_id, "settings")
    login(client, "player", PASSWORD)
    assert client.patch(f"/api/servers/{sid}", json={"disk_limit_mb": 0}).status_code == 403


def test_format_size():
    assert format_size(512 * MB) == "512 MB"
    assert format_size(10 * 1024 * MB) == "10.0 GB"
