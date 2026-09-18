import pytest
from starlette.websockets import WebSocketDisconnect

from app.api import auth as auth_api
from tests.conftest import ADMIN_PASSWORD, add_user, login
from tests.test_files import upload
from tests.test_servers import create_installed

PASSWORD = "player-password"


@pytest.fixture(autouse=True)
def fresh_throttle(monkeypatch):
    monkeypatch.setattr(auth_api, "throttle", auth_api.LoginThrottle())


def grant(client, server_id, user_id, *permissions):
    resp = client.put(f"/api/servers/{server_id}/access/{user_id}", json={"permissions": list(permissions)})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- sessions -------------------------------------------------------------------


def test_login_logout_and_me(client):
    me = client.get("/api/auth/me").json()
    assert me["username"] == "admin" and me["is_admin"] is True
    cookie = client.cookies.get("possum_session")
    assert cookie and len(cookie) > 30

    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.set("possum_session", cookie)  # the old cookie is dead server-side too
    assert client.get("/api/auth/me").status_code == 401


def test_everything_needs_a_login(client):
    client.cookies.clear()
    for url in ("/api/servers", "/api/templates", "/api/meta", "/api/users"):
        assert client.get(url).status_code == 401, url
    assert client.get("/api/health").status_code == 200


def test_wrong_password_and_throttling(client):
    client.cookies.clear()
    bad = {"username": "admin", "password": "nope"}
    assert client.post("/api/auth/login", json=bad).json()["detail"] == "wrong name or password"
    assert client.post("/api/auth/login", json={"username": "ghost", "password": "x"}).status_code == 401
    for _ in range(10):
        client.post("/api/auth/login", json=bad)
    # Even the right password waits now.
    resp = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert resp.status_code == 429


def test_state_changes_need_the_csrf_header(client):
    resp = client.post("/api/servers", json={}, headers={"X-Requested-With": ""})
    assert resp.status_code == 403 and "X-Requested-With" in resp.json()["detail"]


def test_change_password_logs_out_other_sessions(client):
    other = client.cookies.get("possum_session")
    login(client, "admin", ADMIN_PASSWORD)  # a second session, this "browser"
    resp = client.post(
        "/api/auth/password", json={"current_password": "wrong", "new_password": "new-password-1"}
    )
    assert resp.json()["detail"]["errors"] == {"current_password": "wrong password"}
    assert (
        client.post(
            "/api/auth/password", json={"current_password": ADMIN_PASSWORD, "new_password": "new-password-1"}
        ).status_code
        == 204
    )
    assert client.get("/api/auth/me").status_code == 200  # still logged in here
    current = client.cookies.get("possum_session")
    client.cookies.set("possum_session", other)
    assert client.get("/api/auth/me").status_code == 401  # the other session is gone
    client.cookies.set("possum_session", current)


# --- users ----------------------------------------------------------------------


def test_admin_manages_users(client):
    resp = client.post("/api/users", json={"username": "x", "password": "short"})
    assert set(resp.json()["detail"]["errors"]) == {"username", "password"}

    user = client.post("/api/users", json={"username": "Player", "password": PASSWORD}).json()
    assert client.post("/api/users", json={"username": "player", "password": PASSWORD}).status_code == 422

    login(client, "player", PASSWORD)  # names are case-insensitive
    assert client.get("/api/users").status_code == 403
    assert client.post("/api/users", json={"username": "evil", "password": PASSWORD}).status_code == 403

    login(client, "admin", ADMIN_PASSWORD)
    assert client.patch(f"/api/users/{user['id']}", json={"disabled": True}).json()["disabled"] is True
    client.cookies.clear()
    assert (
        client.post("/api/auth/login", json={"username": "player", "password": PASSWORD}).status_code == 403
    )
    login(client, "admin", ADMIN_PASSWORD)
    assert client.delete(f"/api/users/{user['id']}").status_code == 204


def test_email_is_optional_and_belongs_to_one_user(client):
    plain = client.post("/api/users", json={"username": "plain", "password": PASSWORD}).json()
    assert plain["email"] is None

    bad = client.post("/api/users", json={"username": "bad", "password": PASSWORD, "email": "not-an-email"})
    assert set(bad.json()["detail"]["errors"]) == {"email"}

    # Stored lowercase, so the same address can't come back in another spelling.
    owner = client.post(
        "/api/users", json={"username": "owner", "password": PASSWORD, "email": "Owner@Example.COM"}
    ).json()
    assert owner["email"] == "owner@example.com"
    taken = client.post(
        "/api/users", json={"username": "other", "password": PASSWORD, "email": "owner@example.com"}
    )
    assert taken.status_code == 422
    assert client.patch(f"/api/users/{plain['id']}", json={"email": "owner@example.com"}).status_code == 422

    # Keeping your own address is fine; an empty field gives it up.
    assert client.patch(f"/api/users/{owner['id']}", json={"email": "owner@example.com"}).status_code == 200
    assert client.patch(f"/api/users/{owner['id']}", json={"email": ""}).json()["email"] is None
    assert client.patch(f"/api/users/{plain['id']}", json={"email": "owner@example.com"}).status_code == 200

    login(client, "plain", PASSWORD)
    assert client.get("/api/auth/me").json()["email"] == "owner@example.com"


def test_there_is_always_an_active_admin(client):
    me = client.get("/api/auth/me").json()
    assert client.patch(f"/api/users/{me['id']}", json={"is_admin": False}).status_code == 409
    assert client.delete(f"/api/users/{me['id']}").status_code == 409

    other = client.post(
        "/api/users", json={"username": "second", "password": PASSWORD, "is_admin": True}
    ).json()
    assert client.patch(f"/api/users/{other['id']}", json={"is_admin": False}).status_code == 200


def test_disabling_a_user_ends_their_sessions(client):
    user_id = add_user(client, "player", PASSWORD)
    login(client, "player", PASSWORD)
    player_cookie = client.cookies.get("possum_session")
    login(client, "admin", ADMIN_PASSWORD)
    client.patch(f"/api/users/{user_id}", json={"disabled": True})
    client.cookies.set("possum_session", player_cookie)
    assert client.get("/api/auth/me").status_code == 401


# --- per-server permissions -------------------------------------------------------


def test_users_see_only_servers_shared_with_them(client):
    shared = create_installed(client, "cs16", name="Shared")
    secret = create_installed(client, "cs16", name="Secret")
    user_id = add_user(client, "player", PASSWORD)
    grant(client, shared["id"], user_id, "control")

    login(client, "player", PASSWORD)
    assert [s["name"] for s in client.get("/api/servers").json()] == ["Shared"]
    # Someone else's server looks like it doesn't exist.
    assert client.get(f"/api/servers/{secret['id']}").status_code == 404
    assert client.post(f"/api/servers/{secret['id']}/start").status_code == 404
    # Servers are created and deleted by administrators only.
    assert client.post("/api/servers", json={"template_id": "cs16", "name": "Mine"}).status_code == 403
    assert client.delete(f"/api/servers/{shared['id']}").status_code == 403


def test_each_permission_opens_its_own_part(client):
    server = create_installed(client, "minecraft-java", version="1.21.1", eula=True)
    sid = server["id"]
    upload(client, server, "", {"world/level.dat": b"v1"})
    backup = client.post(f"/api/servers/{sid}/backups", json={}).json()
    user_id = add_user(client, "player", PASSWORD)

    checks = {
        "control": lambda: client.post(f"/api/servers/{sid}/start"),
        "files": lambda: client.get(f"/api/servers/{sid}/files"),
        "backups": lambda: client.get(f"/api/servers/{sid}/backups/{backup['id']}/download"),
        "restore": lambda: client.post(f"/api/servers/{sid}/backups/{backup['id']}/restore"),
        "settings": lambda: client.get(f"/api/servers/{sid}/configs"),
        "schedules": lambda: client.get(f"/api/servers/{sid}/schedules"),
    }
    for permission, request in checks.items():
        login(client, "admin", ADMIN_PASSWORD)
        entry = grant(client, sid, user_id, permission)
        assert entry["permissions"][0] == "view"  # any permission implies seeing the server

        login(client, "player", PASSWORD)
        assert client.get(f"/api/servers/{sid}/permissions").json() == ["view", permission]
        assert request().status_code not in (403, 404), permission
        for other, other_request in checks.items():
            if other != permission and other != "restore" and permission != "restore":
                assert other_request().status_code == 403, f"{permission} must not allow {other}"

    login(client, "admin", ADMIN_PASSWORD)
    grant(client, sid, user_id)  # no permissions: access removed
    login(client, "player", PASSWORD)
    assert client.get("/api/servers").json() == []


def test_access_list_for_admins(client):
    server = create_installed(client, "cs16")
    user_id = add_user(client, "player", PASSWORD)
    add_user(client, "boss", PASSWORD, is_admin=True)
    grant(client, server["id"], user_id, "console", "files")
    entries = client.get(f"/api/servers/{server['id']}/access").json()
    # Admins aren't listed: they can do everything anyway.
    assert entries == [
        {
            "user_id": user_id,
            "username": "player",
            "disabled": False,
            "permissions": ["view", "console", "files"],
        }
    ]


def test_console_socket_checks_session_origin_and_permission(client, runtime):
    server = create_installed(client, "cs16")
    client.post(f"/api/servers/{server['id']}/start")
    url = f"/api/servers/{server['id']}/console"
    user_id = add_user(client, "player", PASSWORD)
    grant(client, server["id"], user_id, "control")  # can watch, can't type

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url, headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    assert exc.value.code == 4403

    login(client, "player", PASSWORD)
    with client.websocket_connect(url) as ws:
        ws.send_json({"type": "command", "data": "quit"})
        assert ws.receive_json() == {"type": "error", "data": "you can't send commands to this server"}
    assert (server["id"], "quit") not in runtime.commands

    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url) as ws:
            ws.receive_json()
    assert exc.value.code == 4401
