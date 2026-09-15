from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from app.models import Schedule, ScheduleAction
from app.runtime.scheduler import describe, next_run, validate_cron
from tests.test_backups import wait_for_backup
from tests.test_files import upload
from tests.test_servers import create_installed

KYIV = ZoneInfo("Europe/Kyiv")


def test_next_run_uses_the_panel_time_zone():
    after = datetime(2026, 9, 15, 0, 30, tzinfo=UTC)  # 03:30 in Kyiv
    assert next_run("0 4 * * *", after, KYIV) == datetime(2026, 9, 15, 1, 0, tzinfo=UTC)
    # Strictly after: a run at exactly 04:00 schedules tomorrow's.
    assert next_run("0 4 * * *", datetime(2026, 9, 15, 1, 0, tzinfo=UTC), KYIV).day == 16


@pytest.mark.parametrize("expression", ["61 * * * *", "* * *", "0 4 * * * *", "every day"])
def test_invalid_cron_is_rejected(expression):
    with pytest.raises(ValueError):
        validate_cron(expression, KYIV)


def test_cron_is_described_in_words():
    assert "04:00" in describe("0 4 * * *", KYIV)


def url(server, suffix=""):
    return f"/api/servers/{server['id']}/schedules{suffix}"


def test_create_update_delete(client):
    server = create_installed(client, "cs16")
    resp = client.post(
        url(server), json={"name": "nightly", "cron": "0  4 * * *", "action": "restart", "keep": 5}
    )
    assert resp.status_code == 201, resp.text
    schedule = resp.json()
    assert schedule["cron"] == "0 4 * * *"  # whitespace normalised
    assert schedule["keep"] is None  # only backups keep anything
    assert schedule["next_run_at"] and schedule["description"]

    resp = client.put(url(server, f"/{schedule['id']}"), json={**schedule, "enabled": False})
    assert resp.json()["enabled"] is False and resp.json()["next_run_at"] is None

    assert client.delete(url(server, f"/{schedule['id']}")).status_code == 204
    assert client.get(url(server)).json() == []


def test_errors_are_keyed_by_field(client):
    server = create_installed(client, "cs16")
    resp = client.post(url(server), json={"name": "x", "cron": "99 * * * *", "action": "command"})
    assert resp.status_code == 422
    assert set(resp.json()["detail"]["errors"]) == {"cron", "command"}


def test_meta_reports_the_time_zone(client):
    assert client.get("/api/meta").json()["timezone"]


def test_due_schedules_run_and_move_on(client, runtime):
    server = create_installed(client, "cs16")
    client.post(f"/api/servers/{server['id']}/start")
    schedule = client.post(
        url(server), json={"name": "hi", "cron": "*/5 * * * *", "action": "command", "command": "say hi"}
    ).json()

    due = datetime.fromisoformat(schedule["next_run_at"])
    client.portal.call(client.app.state.scheduler.tick, due + timedelta(seconds=1))

    after = client.get(url(server)).json()[0]
    assert (server["id"], "say hi") in runtime.commands
    assert after["last_status"] == "ok"
    assert datetime.fromisoformat(after["next_run_at"]) == due + timedelta(minutes=5)


def test_actions_that_dont_fit_are_skipped(client, runtime):
    server = create_installed(client, "cs16")  # stopped
    schedule = client.post(url(server), json={"name": "r", "cron": "0 4 * * *", "action": "restart"}).json()
    result = client.post(url(server, f"/{schedule['id']}/run")).json()
    assert result["last_status"] == "skipped" and "isn't running" in result["last_message"]
    # "run now" doesn't touch the timetable.
    assert result["next_run_at"] == schedule["next_run_at"]


def test_scheduled_backups_keep_only_the_newest(client):
    server = create_installed(client, "minecraft-java", version="1.21.1", eula=True)
    upload(client, server, "", {"world/level.dat": b"v1"})
    schedule = client.post(
        url(server), json={"name": "daily", "cron": "0 4 * * *", "action": "backup", "keep": 2}
    ).json()

    manual = wait_for_backup(
        client, server, client.post(f"/api/servers/{server['id']}/backups", json={}).json()["id"]
    )
    for _ in range(3):
        client.post(url(server, f"/{schedule['id']}/run"))
        backups = client.get(f"/api/servers/{server['id']}/backups").json()["backups"]
        wait_for_backup(client, server, backups[0]["id"])

    backups = client.get(f"/api/servers/{server['id']}/backups").json()["backups"]
    scheduled = [b for b in backups if b["schedule_id"] == schedule["id"]]
    assert len(scheduled) == 2 and all(b["note"] == "daily" for b in scheduled)
    assert manual["id"] in {b["id"] for b in backups}  # manual backups are never pruned


def test_missed_runs_are_skipped_after_downtime(client):
    server = create_installed(client, "cs16")
    schedule = client.post(url(server), json={"name": "r", "cron": "0 4 * * *", "action": "restart"}).json()

    async def pretend_panel_was_down():
        async with client.app.state.sessionmaker() as session:
            row = await session.get(Schedule, schedule["id"])
            row.next_run_at = datetime.now(UTC) - timedelta(days=3)
            await session.commit()
        await client.app.state.scheduler.skip_missed()

    client.portal.call(pretend_panel_was_down)
    after = client.get(url(server)).json()[0]
    assert datetime.fromisoformat(after["next_run_at"]) > datetime.now(UTC)
    assert after["last_run_at"] is None


def test_schedule_for_deleted_server_reports_it(client):
    scheduler = client.app.state.scheduler
    orphan = Schedule(server_id="gone", name="x", cron="0 4 * * *", action=ScheduleAction.RESTART)
    assert client.portal.call(scheduler.execute, orphan, None) == ("failed", "the server no longer exists")


def test_time_zone_is_a_real_zone_not_an_offset(monkeypatch):
    from app.runtime.scheduler import resolve_timezone

    assert resolve_timezone("Europe/Kyiv").key == "Europe/Kyiv"
    monkeypatch.setenv("TZ", "America/New_York")
    assert resolve_timezone(None).key == "America/New_York"
    with pytest.raises(ZoneInfoNotFoundError):
        resolve_timezone("Mars/Olympus")
    # Daylight saving: 04:00 in Kyiv is 01:00 UTC in summer and 02:00 UTC in winter.
    winter = next_run("0 4 * * *", datetime(2026, 11, 1, tzinfo=UTC), ZoneInfo("Europe/Kyiv"))
    assert winter.hour == 2
