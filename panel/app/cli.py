"""Terminal commands, run by ./possum inside the panel container.

python -m app.cli ensure-admin   first start: create the administrator if there are no users yet
python -m app.cli create-admin   add an administrator, or reset one's password
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.db import create_engine, run_migrations
from app.core.security import hash_password, password_error, username_error
from app.models import User, UserSession


def ask(prompt: str, secret: bool = False) -> str:
    # Hidden input on a terminal; piped input (scripts, tests) is read line by line.
    if sys.stdin.isatty():
        return getpass.getpass(prompt) if secret else input(prompt)
    print(prompt, end="", flush=True)
    line = sys.stdin.readline()
    if not line:
        raise SystemExit("\ncancelled")
    print()
    return line.rstrip("\n")


def ask_username() -> str:
    while True:
        username = ask("name: ").strip()
        if error := username_error(username):
            print(f"  {error}")
        else:
            return username


def ask_password() -> str:
    while True:
        password = ask("password: ", secret=True)
        if error := password_error(password):
            print(f"  {error}")
            continue
        if ask("repeat password: ", secret=True) != password:
            print("  the passwords don't match")
            continue
        return password


async def create_admin(sessionmaker, *, only_if_no_users: bool) -> None:
    async with sessionmaker() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        if only_if_no_users and users:
            return

        print("Create administrator account" if not users else "Add an administrator or reset a password")
        username = ask_username()
        user = await session.scalar(select(User).where(User.username == username))
        if (
            user
            and ask(f"'{username}' exists. Set a new password and make it an administrator? [y/N] ").lower()
            != "y"
        ):
            print("nothing changed")
            return

        password = ask_password()
        if user is None:
            user = User(username=username)
            session.add(user)
        else:
            # A new password logs the account out everywhere.
            for old in await session.scalars(select(UserSession).where(UserSession.user_id == user.id)):
                await session.delete(old)
        user.password_hash, user.is_admin, user.disabled = hash_password(password), True, False
        await session.commit()
        print(f"administrator '{username}' is ready")


async def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=["ensure-admin", "create-admin"])
    args = parser.parse_args(argv)

    settings = Settings()
    await run_migrations(settings.database_url)
    engine = create_engine(settings.database_url)
    try:
        await create_admin(async_sessionmaker(engine), only_if_no_users=args.command == "ensure-admin")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit("\ncancelled") from None
