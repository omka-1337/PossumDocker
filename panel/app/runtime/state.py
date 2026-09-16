"""What every runtime shares: container state, and the errors that mean Docker couldn't be reached.

Kept apart from app.runtime.docker, which needs aiodocker: an installation that talks to the agent
(every real one) doesn't have to install it.
"""

from collections.abc import Callable
from dataclasses import dataclass
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


try:
    from aiodocker.exceptions import DockerError
except ImportError:  # no development runtime installed
    RUNTIME_ERRORS: tuple[type[Exception], ...] = (RuntimeUnavailable,)
else:
    # Talking to Docker directly (development) can also fail with aiodocker's own error.
    RUNTIME_ERRORS = (RuntimeUnavailable, DockerError)
