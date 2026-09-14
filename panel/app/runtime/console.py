import asyncio
import time
from collections.abc import AsyncIterator
from typing import Protocol

from app.runtime.docker import ContainerState

HISTORY_LINES = 300
POLL_SECONDS = 1.0


class LogSource(Protocol):
    async def state(self, server_id: str) -> ContainerState | None: ...
    def logs(self, server_id: str, tail: int | None = 200, since: int = 0) -> AsyncIterator[str]: ...


async def follow_console(runtime: LogSource, server_id: str) -> AsyncIterator[str]:
    """Console output that survives restarts.

    Docker's log stream ends when the container stops. Instead of ending the console,
    wait for the server to run again and continue from where it left off.
    """
    since = 0
    tail: int | None = HISTORY_LINES
    while True:
        started_at = int(time.time())
        async for line in runtime.logs(server_id, tail=tail, since=since):
            yield line
        # Everything up to now has been shown: next time only newer lines.
        since, tail = max(started_at, int(time.time())), None

        while True:
            # Also a pause when a stream ends while the container still runs, so we never spin.
            await asyncio.sleep(POLL_SECONDS)
            state = await runtime.state(server_id)
            if state and state.status in ("running", "restarting"):
                break
