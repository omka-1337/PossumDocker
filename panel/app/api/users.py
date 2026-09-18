from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.api.deps import Session
from app.core.auth import Admin, CurrentUser, server_permissions
from app.core.permissions import ALL, Permission
from app.core.security import email_error, hash_password, password_error, username_error
from app.models import Server, ServerAccess, User, UserSession

router = APIRouter(tags=["users"])


class UserInfo(BaseModel):
    id: str
    username: str
    # None: none was given; it is optional.
    email: str | None
    is_admin: bool
    disabled: bool
    created_at: datetime
    # Servers they were given access to (administrators see all of them anyway).
    servers: int


class UserCreate(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024)
    email: str | None = Field(default=None, max_length=255)
    is_admin: bool = False


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, max_length=1024)
    # "": remove the address the user has; None: leave it as it is.
    email: str | None = Field(default=None, max_length=255)
    is_admin: bool | None = None
    disabled: bool | None = None


class AccessEntry(BaseModel):
    user_id: str
    username: str
    disabled: bool
    permissions: list[Permission]


class AccessUpdate(BaseModel):
    permissions: list[Permission]


def to_info(user: User, servers: int) -> UserInfo:
    return UserInfo(
        id=user.id,
        username=user.username,
        email=user.email,
        is_admin=user.is_admin,
        disabled=user.disabled,
        created_at=user.created_at,
        servers=servers,
    )


async def _email_or_error(
    session: Session, email: str, user_id: str | None = None
) -> tuple[str | None, str | None]:
    """The address as it will be stored (lowercase, empty means none), or why it can't be."""
    address = email.strip().lower()
    if not address:
        return None, None
    if error := email_error(address):
        return None, error
    taken = await session.scalar(select(User).where(User.email == address, User.id != user_id))
    return address, "already used by another user" if taken else None


async def _user_or_404(session: Session, user_id: str) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    return user


async def _active_admins(session: Session) -> int:
    return (
        await session.scalar(select(func.count()).select_from(User).where(User.is_admin, ~User.disabled)) or 0
    )


@router.get("/users")
async def list_users(session: Session, admin: Admin) -> list[UserInfo]:
    counts = dict(
        (
            await session.execute(select(ServerAccess.user_id, func.count()).group_by(ServerAccess.user_id))
        ).all()
    )
    users = await session.scalars(select(User).order_by(User.created_at))
    return [to_info(u, counts.get(u.id, 0)) for u in users]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, session: Session, admin: Admin) -> UserInfo:
    username = body.username.strip()
    errors = {}
    if error := username_error(username):
        errors["username"] = error
    elif await session.scalar(select(User).where(func.lower(User.username) == username.lower())):
        errors["username"] = "already taken"
    if error := password_error(body.password):
        errors["password"] = error
    email, error = await _email_or_error(session, body.email or "")
    if error:
        errors["email"] = error
    if errors:
        raise HTTPException(422, {"errors": errors})
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(body.password),
        is_admin=body.is_admin,
    )
    session.add(user)
    await session.commit()
    return to_info(user, 0)


@router.patch("/users/{user_id}")
async def update_user(user_id: str, body: UserUpdate, session: Session, admin: Admin) -> UserInfo:
    user = await _user_or_404(session, user_id)
    if user.id == admin.id and (body.is_admin is False or body.disabled):
        raise HTTPException(409, "you can't take away your own administrator rights or disable yourself")
    losing_admin = user.is_admin and not user.disabled and (body.is_admin is False or body.disabled)
    if losing_admin and await _active_admins(session) <= 1:
        raise HTTPException(409, "there must be at least one active administrator")

    logout = False
    if body.password is not None:
        if error := password_error(body.password):
            raise HTTPException(422, {"errors": {"password": error}})
        user.password_hash, logout = hash_password(body.password), True
    if body.email is not None:
        email, error = await _email_or_error(session, body.email, user.id)
        if error:
            raise HTTPException(422, {"errors": {"email": error}})
        user.email = email
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.disabled is not None:
        user.disabled = body.disabled
        logout = logout or body.disabled
    if logout:
        await session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await session.commit()
    servers = await session.scalar(
        select(func.count()).select_from(ServerAccess).where(ServerAccess.user_id == user.id)
    )
    return to_info(user, servers or 0)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, session: Session, admin: Admin) -> None:
    user = await _user_or_404(session, user_id)
    if user.id == admin.id:
        raise HTTPException(409, "you can't delete yourself")
    if user.is_admin and not user.disabled and await _active_admins(session) <= 1:
        raise HTTPException(409, "there must be at least one active administrator")
    await session.delete(user)
    await session.commit()


# --- per-server access -------------------------------------------------------


@router.get("/servers/{server_id}/access")
async def list_access(server_id: str, session: Session, admin: Admin) -> list[AccessEntry]:
    """Every non-admin user with what they may do on this server (nothing, if they have no access)."""
    if await session.get(Server, server_id) is None:
        raise HTTPException(404, "server not found")
    rows = {
        a.user_id: a.permissions
        for a in await session.scalars(select(ServerAccess).where(ServerAccess.server_id == server_id))
    }
    users = await session.scalars(select(User).where(~User.is_admin).order_by(User.username))
    return [
        AccessEntry(user_id=u.id, username=u.username, disabled=u.disabled, permissions=rows.get(u.id, []))
        for u in users
    ]


@router.put("/servers/{server_id}/access/{user_id}")
async def set_access(
    server_id: str, user_id: str, body: AccessUpdate, session: Session, admin: Admin
) -> AccessEntry:
    if await session.get(Server, server_id) is None:
        raise HTTPException(404, "server not found")
    user = await _user_or_404(session, user_id)
    if user.is_admin:
        raise HTTPException(409, "administrators can already do everything")

    # Any permission implies seeing the server; keep them in a stable order.
    wanted = set(body.permissions)
    if wanted:
        wanted.add(Permission.VIEW)
    permissions = [p.value for p in ALL if p in wanted]

    access = await session.get(ServerAccess, (user.id, server_id))
    if not permissions:
        if access:
            await session.delete(access)
    elif access:
        access.permissions = permissions
    else:
        session.add(ServerAccess(user_id=user.id, server_id=server_id, permissions=permissions))
    await session.commit()
    return AccessEntry(
        user_id=user.id, username=user.username, disabled=user.disabled, permissions=permissions
    )


@router.get("/servers/{server_id}/permissions")
async def my_permissions(server_id: str, session: Session, user: CurrentUser) -> list[Permission]:
    """What the logged-in user may do on this server; the UI shows only those tabs and buttons."""
    granted = await server_permissions(session, user, server_id)
    if not granted or await session.get(Server, server_id) is None:
        raise HTTPException(404, "server not found")
    return [Permission(p) for p in granted]
