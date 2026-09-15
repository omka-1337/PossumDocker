"""Passwords and usernames."""

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters

USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD = 8


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with older parameters; re-hash it at the next successful login."""
    return _hasher.check_needs_rehash(password_hash)


def username_error(username: str) -> str | None:
    if not USERNAME.fullmatch(username):
        return "3–32 characters: letters, digits, _ . -"
    return None


def password_error(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"at least {MIN_PASSWORD} characters"
    if len(password) > 1024:
        return "too long"
    return None
