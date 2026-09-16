"""Attacks found in the security review, kept as tests so they stay fixed."""

import pytest

from app.api.auth import LoginThrottle
from app.games.configs import ConfigValuesError, validate_config_values
from app.games.providers import InvalidParams, OptionsProviders
from app.games.schema import ConfigFile
from tests.conftest import ADMIN_PASSWORD, add_user, login
from tests.test_auth import PASSWORD, fresh_throttle, grant  # noqa: F401  (fixture)
from tests.test_servers import create_installed


def schedule(client, sid, **body):
    return client.post(f"/api/servers/{sid}/schedules", json={"name": "s", "cron": "0 4 * * *", **body})


def test_schedules_permission_does_not_open_the_console_or_controls(client, runtime):
    server = create_installed(client, "cs16")
    sid = server["id"]
    client.post(f"/api/servers/{sid}/start")
    admin_command = schedule(client, sid, action="command", command="say hi").json()
    user_id = add_user(client, "player", PASSWORD)
    grant(client, sid, user_id, "schedules")
    login(client, "player", PASSWORD)

    for action, extra in (("command", {"command": "rcon_password pwned"}), ("stop", {}), ("backup", {})):
        assert schedule(client, sid, action=action, **extra).status_code == 403, action
    # Nor by taking over, or running, a schedule an administrator set up.
    url = f"/api/servers/{sid}/schedules/{admin_command['id']}"
    assert client.post(f"{url}/run").status_code == 403
    edited = {"name": "s", "cron": "* * * * *", "action": "command", "command": "rcon_password pwned"}
    assert client.put(url, json=edited).status_code == 403
    assert runtime.commands == []

    login(client, "admin", ADMIN_PASSWORD)
    grant(client, sid, user_id, "schedules", "backups")
    login(client, "player", PASSWORD)
    assert schedule(client, sid, action="backup", keep=3).status_code == 201


@pytest.mark.parametrize("key", ['hostname "x"\nrcon_password', "sv_password;quit", "a b", ""])
def test_config_setting_names_cannot_add_lines(key):
    config = ConfigFile(id="c", label="c", path="server.cfg", format="cvars", managed=["rcon_password"])
    with pytest.raises(ConfigValuesError):
        validate_config_values(config, {key: "x"})


def test_config_only_changes_settings_the_file_or_template_has():
    config = ConfigFile(
        id="c", label="c", path="server.cfg", format="cvars", hints={"hostname": {"label": "Name"}}
    )
    validate_config_values(config, {"hostname": "x"}, existing=set())
    validate_config_values(config, {"sv_gravity": "100"}, existing={"sv_gravity"})
    with pytest.raises(ConfigValuesError):
        validate_config_values(config, {"exec": "evil.cfg"}, existing={"sv_gravity"})


def test_panel_cannot_be_framed(client):
    headers = client.get("/api/health").headers
    assert headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_wrong_passwords_from_elsewhere_do_not_lock_the_owner_out():
    throttle = LoginThrottle()
    for _ in range(50):
        throttle.failed("203.0.113.9", "omka")
    assert throttle.blocked("203.0.113.9", "omka")
    assert not throttle.blocked("198.51.100.4", "omka")


async def test_option_parameters_are_checked_before_leaving_the_panel():
    calls = []

    async def provider(_client, params):
        calls.append(params)
        return []

    providers = OptionsProviders(client=None)
    providers.register("p", provider)
    await providers.get("p", {"version": "1.21.1"})
    with pytest.raises(InvalidParams):
        await providers.get("p", {"version": "../../admin?x=1"})
    assert calls == [{"version": "1.21.1"}]
