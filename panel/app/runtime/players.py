"""Who plays on each server, read from its console output, and kicking and banning them.

A template's `players` section says which console lines mean a player joined or left and which
commands kick and ban. The panel follows every running server's output, keeps what it learns in the
players table, and checks the online list with the game's own status command when asked.
"""

import asyncio
import ipaddress
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from sqlalchemy import delete, select, update

from app.games.schema import BanFile, BanSpec, PlayersSpec, Template
from app.models import BanRecord, Player, Server, ServerState
from app.runtime.spec import _render
from app.runtime.state import RUNTIME_ERRORS, parse_docker_time

if TYPE_CHECKING:
    from app.runtime.manager import ServerManager

log = logging.getLogger(__name__)

# How often a stopped server is checked for having started again.
POLL_SECONDS = 5
# How often the list of servers to follow is refreshed.
SUPERVISE_SECONDS = 15
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Characters that could end or split a console command: never let a name, id or reason carry them.
UNSAFE = re.compile(r'["\\;\x00-\x1f\x7f]')

BanKind = Literal["player", "ip"]
# When a ban or unban takes effect.
Effective = Literal["now", "after restart", "on join"]

# A ban file entry needs something only the game knows, like a Minecraft UUID for a name nobody has seen here.
NOT_ENOUGH_KNOWN = "this player hasn't played on this server yet: start the server to ban them"


class PlayerError(Exception):
    """Something the user asked that this game or this server's state doesn't allow."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass
class BanEntry:
    kind: BanKind
    value: str
    name: str | None = None
    reason: str | None = None
    banned_at: datetime | None = None
    # None: permanent.
    expires_at: datetime | None = None
    banned_by: str | None = None


@dataclass
class ListedBan:
    """An entry of a game's own ban list."""

    value: str
    reason: str | None = None
    created: datetime | None = None
    expires: datetime | None = None


def _minecraft_time(text: object) -> datetime | None:
    """ "2026-09-16 12:00:00 +0000"; "forever" and anything else: None."""
    try:
        return datetime.strptime(str(text), "%Y-%m-%d %H:%M:%S %z").astimezone(UTC)
    except ValueError:
        return None


@dataclass
class ParsedLine:
    on: str
    groups: dict[str, str] = field(default_factory=dict)


def clean_value(value: str, what: str, limit: int = 128) -> str:
    value = value.strip()
    if not value or len(value) > limit or UNSAFE.search(value):
        raise PlayerError(f"invalid {what}", 422)
    return value


def clean_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        raise PlayerError("invalid IP address", 422) from None


def split_timestamp(raw: str) -> tuple[float, str]:
    """A log line from `logs(timestamps=True)`: Docker's time, then the game's text without colours."""
    stamp, _, text = raw.rstrip("\n").partition(" ")
    try:
        when = parse_docker_time(stamp)
    except ValueError:
        when, text = time.time(), raw.rstrip("\n")
    return when, ANSI.sub("", text)


def parse_line(spec: PlayersSpec, text: str) -> ParsedLine | None:
    if spec.ignore and re.search(spec.ignore, text):
        return None
    for event in spec.events:
        if match := re.search(event.pattern, text):
            groups = {k: v.strip() for k, v in match.groupdict().items() if v and v.strip()}
            return ParsedLine(event.type, groups)
    return None


def player_key(spec: PlayersSpec, groups: dict[str, str]) -> str | None:
    game_id, name = groups.get("id"), groups.get("name")
    if spec.key == "id" and game_id and (not spec.id_pattern or re.fullmatch(spec.id_pattern, game_id)):
        return f"id:{game_id}"
    if name:
        return f"name:{name}"
    return None


def _at(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, UTC)


class PlayerService:
    def __init__(self, manager: "ServerManager"):
        self.manager = manager
        self._watchers: dict[str, asyncio.Task] = {}
        self._supervisor: asyncio.Task | None = None
        # What a game said about a connecting player before it knew their id, by (server, slot):
        # CS 1.6 logs the address with a pending SteamID and the SteamID only on "entered the game".
        self._pending: dict[tuple[str, str], dict[str, str]] = {}

    @property
    def _sessionmaker(self):
        return self.manager._sessionmaker

    def spec(self, server: Server) -> PlayersSpec | None:
        template: Template | None = self.manager._templates.get(server.template_id)
        return template.players if template else None

    # --- following consoles ---------------------------------------------------

    def start(self) -> None:
        self._supervisor = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        tasks = [*self._watchers.values(), *([self._supervisor] if self._supervisor else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _supervise(self) -> None:
        """One watcher per installed server whose game tracks players; gone with the server."""
        while True:
            try:
                async with self._sessionmaker() as session:
                    servers = list(
                        await session.scalars(select(Server).where(Server.state == ServerState.INSTALLED))
                    )
                wanted = {s.id for s in servers if (spec := self.spec(s)) and spec.events}
                for server_id in wanted - self._watchers.keys():
                    self._watchers[server_id] = asyncio.create_task(self._watch(server_id))
                for server_id in self._watchers.keys() - wanted:
                    self._watchers.pop(server_id).cancel()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("updating player watchers failed")
            try:
                await self.lift_expired()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("lifting temporary bans failed")
            await asyncio.sleep(SUPERVISE_SECONDS)

    async def _watch(self, server_id: str) -> None:
        runtime = self.manager.runtime
        while True:
            try:
                async with self._sessionmaker() as session:
                    server = await session.get(Server, server_id)
                if server is None:
                    return
                state = await runtime.state(server_id)
                if not state or state.status not in ("running", "restarting"):
                    await self._everyone_offline(server_id, time.time())
                    await asyncio.sleep(POLL_SECONDS)
                    continue
                # This run's whole output: whoever is online now joined since the container started.
                await self._everyone_offline(server_id, None)
                since = int(state.started_at) if state.started_at else 0
                async for raw in runtime.logs(server_id, tail=None, since=since, timestamps=True):
                    await self.handle_line(server, raw)
                # The stream ends when the game stops or crashes.
                await self._everyone_offline(server_id, time.time())
            except asyncio.CancelledError:
                raise
            except RUNTIME_ERRORS as exc:
                log.debug("following players of %s: %s", server_id, exc)
            except Exception:
                log.exception("following players of %s failed", server_id)
            await asyncio.sleep(POLL_SECONDS)

    async def handle_line(self, server: Server, raw: str) -> None:
        spec = self.spec(server)
        if not spec:
            return
        when, text = split_timestamp(raw)
        # Most lines are about something else: only those about players reach the database.
        parsed = parse_line(spec, text)
        if not parsed:
            return
        server_id, at = server.id, _at(when)
        if spec.key == "id" and "slot" in parsed.groups:
            slot = (server_id, parsed.groups["slot"])
            game_id = parsed.groups.get("id")
            id_known = bool(game_id and (not spec.id_pattern or re.fullmatch(spec.id_pattern, game_id)))
            if parsed.on == "info" and not id_known:
                self._pending[slot] = parsed.groups
                return
            if parsed.on in ("join", "info"):
                parsed.groups = {**self._pending.pop(slot, {}), **parsed.groups}
                if not id_known and game_id:
                    parsed.groups["id"] = game_id
        async with self._sessionmaker() as session:
            if parsed.on == "name":
                player = await session.scalar(
                    select(Player)
                    .where(Player.server_id == server_id, Player.online, Player.name.is_(None))
                    .order_by(Player.online_since.desc())
                )
                if player and parsed.groups.get("name"):
                    player.name = parsed.groups["name"]
                    await session.commit()
                return

            key = player_key(spec, parsed.groups)
            if not key:
                return
            player = await session.scalar(
                select(Player).where(Player.server_id == server_id, Player.key == key)
            )
            if player is None:
                if parsed.on == "leave":
                    return
                player = Player(server_id=server_id, key=key, first_seen=at, last_seen=at)
                session.add(player)
            if "name" in parsed.groups:
                player.name = parsed.groups["name"]
            game_id = parsed.groups.get("id")
            # A LAN or bot id says nothing about who it is: don't keep it.
            if game_id and (not spec.id_pattern or re.fullmatch(spec.id_pattern, game_id)):
                player.game_id = game_id
            if "ip" in parsed.groups:
                player.ip = parsed.groups["ip"]
            if "slot" in parsed.groups:
                player.slot = parsed.groups["slot"]

            if parsed.on == "join":
                player.online, player.online_since, player.last_seen = True, at, at
            elif parsed.on == "leave":
                player.online, player.slot = False, None
                player.last_seen = at
            await session.commit()

            if parsed.on == "join":
                await self._enforce_panel_bans(server, spec, player)

    async def _everyone_offline(self, server_id: str, when: float | None) -> None:
        values: dict = {"online": False, "slot": None}
        if when is not None:
            values["last_seen"] = _at(when)
        async with self._sessionmaker() as session:
            await session.execute(
                update(Player).where(Player.server_id == server_id, Player.online).values(**values)
            )
            await session.commit()

    # --- checking with the game -----------------------------------------------

    async def refresh(self, server: Server) -> None:
        """Ask the game who is online and correct the list: it knows better than old log lines."""
        spec = self.spec(server)
        if not spec or not spec.status:
            raise PlayerError("this game can't list its players")
        await self._require_running(server)
        runtime = self.manager.runtime
        status = spec.status
        asked = time.time()
        online: dict[str, dict[str, str]] = {}
        answered = asyncio.Event()

        def groups_of(match: re.Match) -> dict[str, str]:
            return {k: v.strip() for k, v in match.groupdict().items() if v and v.strip()}

        async def collect() -> None:
            # From just before the command, so an answer printed before the stream opened isn't missed.
            async for raw in runtime.logs(server.id, tail=None, since=int(asked) - 1, timestamps=True):
                when, text = split_timestamp(raw)
                if when < asked - 1:
                    continue
                if status.names and (match := re.search(status.names, text)):
                    for name in (match.group("names") or "").split(status.separator):
                        if name.strip():
                            online[f"name:{name.strip()}"] = {"name": name.strip()}
                    answered.set()
                    return
                if status.answer and re.search(status.answer, text):
                    answered.set()
                elif answered.is_set() and status.row and (match := re.search(status.row, text)):
                    if spec.ignore and re.search(spec.ignore, text):
                        continue
                    if key := player_key(spec, groups_of(match)):
                        online[key] = groups_of(match)

        reader = asyncio.create_task(collect())
        try:
            await asyncio.sleep(0.2)  # let the stream open first
            await runtime.send_command(server.id, status.command)
            try:
                await asyncio.wait_for(answered.wait(), status.wait)
            except TimeoutError:
                raise PlayerError("the game didn't answer, try again") from None
            if status.row:
                await asyncio.sleep(0.7)  # the rows follow the heading
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

        now = _at(time.time())
        async with self._sessionmaker() as session:
            players = {
                p.key: p for p in await session.scalars(select(Player).where(Player.server_id == server.id))
            }
            for key, groups in online.items():
                player = players.get(key)
                if player is None:
                    player = Player(server_id=server.id, key=key, first_seen=now)
                    session.add(player)
                if not player.online:
                    player.online, player.online_since = True, now
                player.last_seen = now
                player.name = groups.get("name", player.name)
                player.ip = groups.get("ip", player.ip)
                player.slot = groups.get("slot", player.slot)
                if "id" in groups:
                    player.game_id = groups["id"]
            for key, player in players.items():
                if player.online and key not in online:
                    player.online, player.slot, player.last_seen = False, None, now
            await session.commit()

    # --- kick and ban -----------------------------------------------------------

    async def _require_running(self, server: Server) -> None:
        from app.runtime.manager import ServerStatus

        if await self.manager.status_of(server) not in (ServerStatus.RUNNING, ServerStatus.STARTING):
            raise PlayerError("the server is not running")

    async def _is_running(self, server: Server) -> bool:
        try:
            await self._require_running(server)
            return True
        except PlayerError:
            return False

    async def get_player(self, server: Server, key: str) -> Player:
        async with self._sessionmaker() as session:
            player = await session.scalar(
                select(Player).where(Player.server_id == server.id, Player.key == key)
            )
        if player is None:
            raise PlayerError("no such player", 404)
        return player

    @staticmethod
    def _context(player: Player | None = None, **extra: str | None) -> dict:
        context = {
            "name": player.name if player else None,
            "id": player.game_id if player else None,
            "ip": player.ip if player else None,
            "slot": player.slot if player else None,
            "reason": None,
            # Minecraft's ban files: "2026-09-16 12:00:00 +0000"
            "now": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S +0000"),
            "expires": "forever",
        }
        context.update({k: v for k, v in extra.items() if v is not None})
        return context

    async def _send(self, server: Server, commands: list[str], context: dict) -> None:
        rendered = [_render(command, context, strict=True).strip() for command in commands]
        for command in rendered:
            if "\n" in command:
                raise PlayerError("invalid command", 422)
        for command in rendered:
            await self.manager.runtime.send_command(server.id, command)

    async def kick(self, server: Server, key: str, reason: str | None = None) -> None:
        spec = self.spec(server)
        if not spec or not spec.kick:
            raise PlayerError("this game has no way to kick players from outside")
        await self._require_running(server)
        player = await self.get_player(server, key)
        if not player.online:
            raise PlayerError("the player isn't online")
        await self._send_kick(server, spec, player, reason)

    async def _send_kick(self, server: Server, spec: PlayersSpec, player: Player, reason: str | None) -> None:
        try:
            await self._send(server, spec.kick, self._context(player, reason=reason))
        except Exception as exc:  # a template value this player doesn't have (no slot yet)
            raise PlayerError(f"can't kick this player: {exc}") from exc

    def _ban_spec(self, server: Server, kind: BanKind) -> BanSpec:
        spec = self.spec(server)
        ban = (spec.bans if kind == "player" else spec.ip_bans) if spec else None
        if ban is None:
            raise PlayerError("this game can't ban " + ("players" if kind == "player" else "IP addresses"))
        return ban

    async def _records(self, server_id: str) -> dict[tuple[str, str], BanRecord]:
        async with self._sessionmaker() as session:
            rows = await session.scalars(select(BanRecord).where(BanRecord.server_id == server_id))
            return {(row.kind, row.value): row for row in rows}

    async def list_bans(self, server: Server) -> list[BanEntry]:
        spec = self.spec(server)
        if not spec:
            return []
        async with self._sessionmaker() as session:
            players = list(await session.scalars(select(Player).where(Player.server_id == server.id)))
        by_id = {p.game_id: p for p in players if p.game_id}
        records = await self._records(server.id)
        entries: list[BanEntry] = []
        for kind, ban in (("player", spec.bans), ("ip", spec.ip_bans)):
            if ban is None:
                continue
            if ban.by_panel:
                for (record_kind, value), record in records.items():
                    if record_kind == kind:
                        name = value.split(":", 1)[1] if value.startswith("name:") else None
                        entries.append(self._entry(kind, value, name, None, record))
            elif ban.file:
                listed = await self._read_ban_file(server, ban.file)
                for item in listed:
                    name = by_id[item.value].name if ban.by == "id" and item.value in by_id else None
                    entries.append(self._entry(kind, item.value, name, item, records.get((kind, item.value))))
                # Lifted in the game (or by hand): forget the panel's details, but not a ban just sent.
                values = {item.value for item in listed}
                stale = [
                    r.id
                    for (k, v), r in records.items()
                    if k == kind and v not in values and time.time() - r.created_at.timestamp() > 60
                ]
                if stale:
                    async with self._sessionmaker() as session:
                        await session.execute(delete(BanRecord).where(BanRecord.id.in_(stale)))
                        await session.commit()
        return entries

    @staticmethod
    def _entry(
        kind: BanKind, value: str, name: str | None, listed: "ListedBan | None", record: BanRecord | None
    ) -> BanEntry:
        return BanEntry(
            kind,
            value,
            name=name,
            reason=(record.reason if record else None) or (listed.reason if listed else None),
            banned_at=record.created_at if record else (listed.created if listed else None),
            expires_at=record.expires_at if record else (listed.expires if listed else None),
            banned_by=record.banned_by if record else None,
        )

    async def _read_ban_file(self, server: Server, ban_file: BanFile) -> list["ListedBan"]:
        data = await self.manager.read_game_file(server, ban_file.path)
        if not data:
            return []
        text = data.decode("utf-8", errors="replace")
        values: list[ListedBan] = []
        if ban_file.format == "json":
            try:
                items = json.loads(text)
            except ValueError:
                return []
            for item in items if isinstance(items, list) else []:
                if isinstance(item, str):
                    values.append(ListedBan(item))
                elif isinstance(item, dict) and ban_file.field and isinstance(item.get(ban_file.field), str):
                    # Minecraft keeps "created" and "expires" ("forever" or a time) in its lists.
                    values.append(
                        ListedBan(
                            item[ban_file.field],
                            reason=item.get("reason") or None,
                            created=_minecraft_time(item.get("created")),
                            expires=_minecraft_time(item.get("expires")),
                        )
                    )
        else:
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "//")):
                    continue
                if ban_file.pattern:
                    if match := re.search(ban_file.pattern, line):
                        values.append(ListedBan(match.group(1)))
                else:
                    values.append(ListedBan(line))
        return values

    async def _edit_ban_file(
        self, server: Server, ban_file: BanFile, value: str, context: dict | None
    ) -> None:
        """Add an entry (context given) or remove every entry for `value`."""
        data = await self.manager.read_game_file(server, ban_file.path) or b""
        text = data.decode("utf-8", errors="replace")
        known = {k: v for k, v in (context or {}).items() if v is not None or k == "reason"}
        if ban_file.format == "json":
            try:
                items = json.loads(text) if text.strip() else []
            except ValueError as exc:
                raise PlayerError(f"{ban_file.path} isn't valid JSON") from exc
            if not isinstance(items, list):
                raise PlayerError(f"{ban_file.path} isn't a list")

            def matches(item) -> bool:
                return item == value or (isinstance(item, dict) and item.get(ban_file.field) == value)

            items = [item for item in items if not matches(item)]
            if context is not None:
                try:
                    items.append({k: _render(v, known, strict=True) for k, v in ban_file.entry.items()})
                except Exception as exc:
                    raise PlayerError(NOT_ENOUGH_KNOWN) from exc
            output = json.dumps(items, indent=2, ensure_ascii=False) + "\n"
        else:
            lines = []
            for line in text.splitlines():
                stripped = line.strip()
                current = stripped
                if ban_file.pattern and (match := re.search(ban_file.pattern, stripped)):
                    current = match.group(1)
                if stripped and current == value:
                    continue
                lines.append(line)
            if context is not None:
                try:
                    lines.append(_render(ban_file.line, known, strict=True))
                except Exception as exc:
                    raise PlayerError(NOT_ENOUGH_KNOWN) from exc
            output = "\n".join(lines) + "\n" if lines else ""
        await self.manager.write_game_file(server, ban_file.path, output.encode())

    async def ban(
        self,
        server: Server,
        kind: BanKind,
        *,
        key: str | None = None,
        value: str | None = None,
        reason: str | None = None,
        minutes: int | None = None,
        banned_by: str | None = None,
    ) -> Effective:
        """Ban a known player (key) or a value typed in (a name, an id, an address).

        `minutes`: a temporary ban, lifted by the panel once it's over. Returns when it takes effect.
        """
        ban = self._ban_spec(server, kind)
        spec = self.spec(server)
        reason = clean_value(reason, "reason", 200) if reason else None
        player = await self.get_player(server, key) if key else None
        expires_at = datetime.now(UTC) + timedelta(minutes=minutes) if minutes else None
        # Minecraft's own lists take an end time, so the game lifts it even if the panel is down.
        expires = expires_at.strftime("%Y-%m-%d %H:%M:%S +0000") if expires_at else "forever"

        if kind == "ip":
            target = clean_ip(value if value else (player.ip if player and player.ip else ""))
            context = self._context(player, ip=target, reason=reason, expires=expires)
        elif player:
            target = player.game_id if ban.by == "id" else player.name
            if not target:
                raise PlayerError(f"the game hasn't told this player's {ban.by} yet")
            context = self._context(player, reason=reason, expires=expires)
        else:
            target = clean_value(value or "", "name" if ban.by == "name" else "id")
            async with self._sessionmaker() as session:
                column = Player.game_id if ban.by == "id" else Player.name
                player = await session.scalar(
                    select(Player).where(Player.server_id == server.id, column == target)
                )
            context = self._context(
                player, **{"id" if ban.by == "id" else "name": target, "reason": reason, "expires": expires}
            )
        for label in ("name", "id", "slot"):
            if context.get(label):
                clean_value(context[label], label)

        running = await self._is_running(server)
        if ban.by_panel:
            target = (player.key if player else f"{ban.by}:{target}") if kind == "player" else target
            effective: Effective = "on join"
        elif running and ban.add:
            await self._send(server, ban.add, context)
            effective = "now"
        elif ban.file and ban.file.writable:
            await self._edit_ban_file(server, ban.file, target, context)
            effective = "after restart" if running and ban.file_needs_restart else "now"
        else:
            raise PlayerError("start the server to ban players on this game")

        async with self._sessionmaker() as session:
            await session.execute(
                delete(BanRecord).where(
                    BanRecord.server_id == server.id, BanRecord.kind == kind, BanRecord.value == target
                )
            )
            session.add(
                BanRecord(
                    server_id=server.id,
                    kind=kind,
                    value=target,
                    reason=reason,
                    expires_at=expires_at,
                    banned_by=banned_by,
                )
            )
            await session.commit()
        if ban.by_panel and running and player and player.online:
            await self._send_kick(server, spec, player, reason)
        return effective

    async def unban(self, server: Server, kind: BanKind, value: str) -> Effective:
        ban = self._ban_spec(server, kind)
        value = clean_ip(value) if kind == "ip" and not ban.by_panel else clean_value(value, "value", 200)
        running = await self._is_running(server)
        effective: Effective = "now"
        if not ban.by_panel:
            context = self._context(**{"ip" if kind == "ip" else ban.by: value})
            if running and ban.remove:
                await self._send(server, ban.remove, context)
            elif ban.file and ban.file.writable:
                await self._edit_ban_file(server, ban.file, value, None)
                if running and ban.file_needs_restart:
                    effective = "after restart"
            else:
                raise PlayerError("start the server to unban players on this game")
        async with self._sessionmaker() as session:
            await session.execute(
                delete(BanRecord).where(
                    BanRecord.server_id == server.id, BanRecord.kind == kind, BanRecord.value == value
                )
            )
            await session.commit()
        return effective

    async def lift_expired(self) -> None:
        """End temporary bans whose time is up. One that can't be lifted yet (the game needs to run
        for that) is tried again on the next round."""
        async with self._sessionmaker() as session:
            due = list(
                await session.scalars(
                    select(BanRecord).where(
                        BanRecord.expires_at.is_not(None), BanRecord.expires_at <= _at(time.time())
                    )
                )
            )
        from app.runtime.manager import ServerBusy

        for record in due:
            async with self._sessionmaker() as session:
                server = await session.get(Server, record.server_id)
            if server is None:
                continue
            try:
                await self.unban(server, record.kind, record.value)
                log.info("temporary ban of %s on %s is over", record.value, server.id)
            except (PlayerError, ServerBusy, ValueError, *RUNTIME_ERRORS) as exc:
                log.debug("can't lift the ban of %s on %s yet: %s", record.value, server.id, exc)

    async def _enforce_panel_bans(self, server: Server, spec: PlayersSpec, player: Player) -> None:
        """Games without bans: kick a banned player the moment they join."""
        checks = []
        if spec.bans and spec.bans.by_panel:
            checks.append(("player", player.key))
        if spec.ip_bans and spec.ip_bans.by_panel and player.ip:
            checks.append(("ip", player.ip))
        if not checks:
            return
        now = _at(time.time())
        async with self._sessionmaker() as session:
            for kind, value in checks:
                row = await session.scalar(
                    select(BanRecord).where(
                        BanRecord.server_id == server.id, BanRecord.kind == kind, BanRecord.value == value
                    )
                )
                if row and (row.expires_at is None or row.expires_at > now):
                    try:
                        await self._send_kick(server, spec, player, row.reason or "banned")
                    except PlayerError:
                        log.warning("couldn't kick banned player %s on %s", player.key, server.id)
                    return
