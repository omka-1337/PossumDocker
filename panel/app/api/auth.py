from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.api.deps import Session
from app.core.auth import (
    COOKIE,
    CSRF_HEADER,
    CSRF_VALUE,
    CurrentUser,
    LoginThrottle,
    create_session,
    dummy_hash,
    set_session_cookie,
    token_id,
)
from app.core.security import hash_password, needs_rehash, password_error, verify_password
from app.models import User, UserSession

router = APIRouter(prefix="/auth", tags=["auth"])
throttle = LoginThrottle()


class UserRead(BaseModel):
    id: str
    username: str
    is_admin: bool


class LoginBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024)


class PasswordBody(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response, session: Session) -> UserRead:
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(403, "missing X-Requested-With header")
    address = request.client.host if request.client else "unknown"
    if throttle.blocked(address, body.username):
        raise HTTPException(429, "too many failed logins, try again in a few minutes")

    user = await session.scalar(
        select(User).where(func.lower(User.username) == body.username.strip().lower())
    )
    valid = verify_password(user.password_hash if user else dummy_hash(), body.password)
    if not user or not valid:
        throttle.failed(address, body.username)
        raise HTTPException(401, "wrong name or password")
    if user.disabled:
        raise HTTPException(403, "this account is disabled")

    throttle.succeeded(body.username)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    token = await create_session(session, user)
    await session.commit()
    set_session_cookie(response, request, token)
    return UserRead(id=user.id, username=user.username, is_admin=user.is_admin)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, session: Session, user: CurrentUser) -> None:
    if token := request.cookies.get(COOKIE):
        await session.execute(delete(UserSession).where(UserSession.id == token_id(token)))
        await session.commit()
    response.delete_cookie(COOKIE, path="/")


@router.get("/me")
async def me(user: CurrentUser) -> UserRead:
    return UserRead(id=user.id, username=user.username, is_admin=user.is_admin)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: PasswordBody, request: Request, session: Session, user: CurrentUser) -> None:
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(422, {"errors": {"current_password": "wrong password"}})
    if error := password_error(body.new_password):
        raise HTTPException(422, {"errors": {"new_password": error}})
    user.password_hash = hash_password(body.new_password)
    # Log out everywhere else; this browser stays logged in.
    current = token_id(request.cookies.get(COOKIE, ""))
    await session.execute(
        delete(UserSession).where(UserSession.user_id == user.id, UserSession.id != current)
    )
    await session.commit()
