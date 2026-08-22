from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.security import MAX_PASSWORD_LENGTH

_HASHER = PasswordHasher()


def verify_admin_password(password: str, encoded_hash: str) -> bool:
    if not password or len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        return _HASHER.verify(encoded_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
