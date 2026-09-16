"""What every runtime shares: container state, and the errors that mean Docker couldn't be reached.

Kept apart from app.runtime.docker, which needs aiodocker: an installation that talks to the agent
(every real one) doesn't have to install it.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

LogFn = Callable[[str], None]


class RuntimeUnavailable(Exception):
    """Docker can't be reached (not running, no access to the socket...)."""


@dataclass(frozen=True)
class ContainerState:
    status: Literal["created", "running", "paused", "restarting", "removing", "exited", "dead"]
    # "starting" | "healthy" | "unhealthy" when the image has a healthcheck, else None.
    health: str | None = None
    # How the last run ended, once it has.
    exit_code: int | None = None
    oom_killed: bool = False
    # When the container last started (unix seconds); only from a single server's state.
    started_at: float | None = None


def parse_docker_time(text: str) -> float:
    """Docker's RFC 3339 times with nanoseconds ("2026-09-16T17:00:00.123456789Z") as unix seconds."""
    match = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)", text.strip())
    if not match:
        raise ValueError(f"not a Docker time: {text!r}")
    whole, fraction, zone = match.groups()
    micro = f".{fraction[:6]}" if fraction else ""
    return datetime.fromisoformat(whole + micro + ("+00:00" if zone == "Z" else zone)).timestamp()


try:
    from aiodocker.exceptions import DockerError
except ImportError:  # no development runtime installed
    RUNTIME_ERRORS: tuple[type[Exception], ...] = (RuntimeUnavailable,)
else:
    # Talking to Docker directly (development) can also fail with aiodocker's own error.
    RUNTIME_ERRORS = (RuntimeUnavailable, DockerError)
