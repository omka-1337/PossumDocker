"""Sessions, the logged-in user and per-server permissions."""

import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.permissions import ALL, Permission
from app.core.security import hash_password
from app.models import ServerAccess, User, UserSession

COOKIE = "possum_session"
SESSION_LIFETIME = timedelta(days=30)
# Refresh a session's expiry at most this often, not on every request.
TOUCH_EVERY = timedelta(minutes=10)
# Browsers only send this header from our own scripts: a form on another site can't add it (CSRF).
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "possum"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Checked when the user doesn't exist, so a login takes as long either way.
_DUMMY_HASH = hash_password("not a real password, only here to spend the same time")


def token_id(token: str) -> str:
    """Sessions are stored by the hash of their token: a leaked database logs nobody in."""
    return hashlib.sha256(token.encode()).hexdigest()


def dummy_hash() -> str:
    return _DUMMY_HASH


def is_https(request: Request) -> bool:
    # Behind a reverse proxy uvicorn takes the scheme from X-Forwarded-Proto, but only from proxies
    # listed in FORWARDED_ALLOW_IPS: anyone else could claim anything.
    return request.url.scheme == "https"


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,  # scripts (and XSS) can't read it
        samesite="lax",
        secure=is_https(request),
        path="/",
    )


async def create_session(session: AsyncSession, user: User) -> str:
    now = datetime.now(UTC)
    await session.execute(delete(UserSession).where(UserSession.expires_at < now))  # tidy up
    token = secrets.token_urlsafe(32)
    session.add(UserSession(id=token_id(token), user_id=user.id, expires_at=now + SESSION_LIFETIME))
    return token


async def user_for_token(session: AsyncSession, token: str | None) -> tuple[User, UserSession] | None:
    if not token:
        return None
    row = await session.get(UserSession, token_id(token))
    if row is None or row.expires_at < datetime.now(UTC):
        return None
    user = await session.get(User, row.user_id)
    if user is None or user.disabled:
        return None
    return user, row


async def current_user(
    request: Request, response: Response, session: Annotated[AsyncSession, Depends(get_session)]
) -> User:
    if request.method in UNSAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(403, "missing X-Requested-With header")
    token = request.cookies.get(COOKIE)
    found = await user_for_token(session, token)
    if found is None:
        raise HTTPException(401, "log in first")
    user, row = found
    now = datetime.now(UTC)
    if now - row.last_seen_at > TOUCH_EVERY:
        # Sliding expiry: someone using the panel stays logged in.
        row.last_seen_at, row.expires_at = now, now + SESSION_LIFETIME
        await session.commit()
        set_session_cookie(response, request, token)
    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(403, "only administrators can do this")
    return user


Admin = Annotated[User, Depends(require_admin)]


async def server_permissions(session: AsyncSession, user: User, server_id: str) -> list[str]:
    if user.is_admin:
        return [p.value for p in ALL]
    access = await session.get(ServerAccess, (user.id, server_id))
    if access is None or Permission.VIEW not in access.permissions:
        return []
    return [p for p in access.permissions if p in {q.value for q in ALL}]


def allow(*permissions: Permission):
    """Route dependency: the logged-in user may do one of `permissions` on the {server_id} in the path.

    A server the user can't even see answers 404, so its existence isn't revealed.
    """

    async def check(
        server_id: str, user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)]
    ) -> None:
        granted = await server_permissions(session, user, server_id)
        if not granted:
            raise HTTPException(404, "server not found")
        if not any(p in granted for p in permissions):
            names = " or ".join(f"'{p.value}'" for p in permissions)
            raise HTTPException(403, f"you need the {names} permission on this server")

    return Depends(check)


async def visible_server_ids(session: AsyncSession, user: User) -> set[str] | None:
    """None: every server (administrators)."""
    if user.is_admin:
        return None
    rows = await session.scalars(select(ServerAccess).where(ServerAccess.user_id == user.id))
    return {row.server_id for row in rows if Permission.VIEW in row.permissions}


class LoginThrottle:
    """Slows down password guessing: a few failures per address, and per name from that address.

    Not per name alone: then anyone could lock the administrator out by getting their password wrong.
    """

    WINDOW = 600  # seconds
    PER_ADDRESS = 20
    PER_USERNAME = 8

    def __init__(self):
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str) -> deque[float]:
        entries = self._failures[key]
        while entries and time.monotonic() - entries[0] > self.WINDOW:
            entries.popleft()
        return entries

    def blocked(self, address: str, username: str) -> bool:
        return (
            len(self._recent(f"ip:{address}")) >= self.PER_ADDRESS
            or len(self._recent(self._user_key(address, username))) >= self.PER_USERNAME
        )

    def failed(self, address: str, username: str) -> None:
        now = time.monotonic()
        self._recent(f"ip:{address}").append(now)
        self._recent(self._user_key(address, username)).append(now)
        # Forget keys with nothing recent, so random names don't pile up in memory.
        for key in [k for k, entries in self._failures.items() if not self._recent(k)]:
            del self._failures[key]

    def succeeded(self, address: str, username: str) -> None:
        self._failures.pop(self._user_key(address, username), None)

    @staticmethod
    def _user_key(address: str, username: str) -> str:
        return f"user:{address}:{username.strip().lower()}"
