import io
import tarfile

from tests.conftest import wait_for_status
from tests.test_files import listing, upload
from tests.test_servers import create_installed


def wait_for_backup(client, server, backup_id, timeout=5):
    import time

    deadline = time.monotonic() + timeout
    while True:
        backup = next(b for b in client.get(url(server)).json()["backups"] if b["id"] == backup_id)
        if backup["status"] != "creating" or time.monotonic() > deadline:
            return backup
        time.sleep(0.02)


def url(server, suffix=""):
    return f"/api/servers/{server['id']}/backups{suffix}"


def test_minecraft_backup_skips_caches_and_restores(client, runtime, tmp_path):
    server = create_installed(client, "minecraft-java", version="1.21.1", eula=True)
    upload(
        client, server, "", {"world/level.dat": b"v1", "libraries/big.jar": b"x", "server.properties": b"a"}
    )

    resp = client.post(url(server), json={"note": "  before update  "})
    assert resp.status_code == 202 and resp.json()["status"] == "creating"
    backup = wait_for_backup(client, server, resp.json()["id"])
    assert backup["status"] == "ready" and backup["note"] == "before update"
    assert backup["paths"] is None and backup["size"] > 0

    archive = tmp_path / "backups" / server["id"] / f"{backup['id']}.tar.gz"
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "./world" in names and "./libraries" not in names  # template excludes libraries

    # Break things, then restore: the world comes back, files added later are gone.
    upload(client, server, "", {"world/level.dat": b"v2-broken", "junk.txt": b"x"})
    assert client.post(url(server, f"/{backup['id']}/restore")).status_code == 202
    assert wait_for_status(client, server["id"], "stopped")["status"] == "stopped"
    assert (
        client.get(f"/api/servers/{server['id']}/files/content", params={"path": "world/level.dat"}).json()[
            "content"
        ]
        == "v1"
    )
    names = listing(client, server)
    assert "junk.txt" not in names
    # Excluded from the backup, so kept: the server must not need to download them again.
    assert names["libraries"] == "dir"


def test_cs16_backup_keeps_only_admin_files(client, runtime):
    server = create_installed(client, "cs16")
    upload(
        client,
        server,
        "",
        {"cstrike/server.cfg": b"hostname x", "cstrike/maps/de_x.bsp": b"m", "hlds_linux": b"elf"},
    )

    backup = wait_for_backup(client, server, client.post(url(server), json={}).json()["id"])
    # Missing paths (addons, motd.txt...) are skipped, and restore touches only what's inside.
    assert backup["paths"] == ["cstrike/maps", "cstrike/server.cfg"]

    upload(client, server, "", {"cstrike/server.cfg": b"hostname changed"})
    client.post(url(server, f"/{backup['id']}/restore"))
    wait_for_status(client, server["id"], "stopped")
    assert listing(client, server)["hlds_linux"] == "file"  # game files untouched
    cfg = client.get(f"/api/servers/{server['id']}/files/content", params={"path": "cstrike/server.cfg"})
    assert cfg.json()["content"] == "hostname x"


def test_backup_with_nothing_to_save_fails_clearly(client):
    server = create_installed(client, "cs16")
    backup = wait_for_backup(client, server, client.post(url(server), json={}).json()["id"])
    assert backup["status"] == "failed" and "nothing to back up" in backup["message"]


def test_restore_needs_a_stopped_server(client):
    server = create_installed(client, "minecraft-java", version="1.21.1", eula=True)
    upload(client, server, "", {"world/level.dat": b"v1"})
    backup = wait_for_backup(client, server, client.post(url(server), json={}).json()["id"])

    client.post(f"/api/servers/{server['id']}/start")
    resp = client.post(url(server, f"/{backup['id']}/restore"))
    assert resp.status_code == 409 and "stop the server" in resp.json()["detail"]


def test_running_server_gets_save_commands(client, runtime):
    server = create_installed(client, "minecraft-java", version="1.21.1", eula=True)
    upload(client, server, "", {"world/level.dat": b"v1"})
    client.post(f"/api/servers/{server['id']}/start")
    client.app.state.manager._templates["minecraft-java"].backup.wait = 0

    wait_for_backup(client, server, client.post(url(server), json={}).json()["id"])
    commands = [c for s, c in runtime.commands if s == server["id"]]
    assert commands == ["save-off", "save-all flush", "save-on"]


def test_download_and_delete(client, tmp_path):
    server = create_installed(client, "minecraft-java", version="1.21.1", eula=True)
    upload(client, server, "", {"world/level.dat": b"v1"})
    backup = wait_for_backup(client, server, client.post(url(server), json={}).json()["id"])

    resp = client.get(url(server, f"/{backup['id']}/download"))
    assert resp.status_code == 200 and "Survival-" in resp.headers["content-disposition"]
    with tarfile.open(fileobj=io.BytesIO(resp.content)) as tar:
        assert "./world/level.dat" in tar.getnames()

    listed = client.get(url(server)).json()
    assert listed["total_size"] == backup["size"]

    assert client.delete(url(server, f"/{backup['id']}")).status_code == 204
    assert client.get(url(server)).json()["backups"] == []
    assert not (tmp_path / "backups" / server["id"] / f"{backup['id']}.tar.gz").exists()


def test_deleting_the_server_removes_its_backups(client, tmp_path):
    server = create_installed(client, "minecraft-java", version="1.21.1", eula=True)
    upload(client, server, "", {"world/level.dat": b"v1"})
    wait_for_backup(client, server, client.post(url(server), json={}).json()["id"])

    assert client.delete(f"/api/servers/{server['id']}").status_code == 204
    assert not (tmp_path / "backups" / server["id"]).exists()
