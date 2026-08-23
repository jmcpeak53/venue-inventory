from __future__ import annotations

import hashlib
import hmac
import re
import secrets

ACCESS_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
ACCESS_CODE_LENGTH = 12
ACCESS_CODE_GROUP_LENGTH = 4
MAX_ACCESS_CODE_INPUT_LENGTH = 64
_NORMALIZE_RE = re.compile(r"[\s-]+")


def generate_access_code() -> str:
    return "".join(
        secrets.choice(ACCESS_CODE_ALPHABET) for _ in range(ACCESS_CODE_LENGTH)
    )


def normalize_access_code(value: str) -> str:
    return _NORMALIZE_RE.sub("", value).upper()


def is_valid_access_code(value: str) -> bool:
    if len(value) > MAX_ACCESS_CODE_INPUT_LENGTH:
        return False
    normalized = normalize_access_code(value)
    return len(normalized) == ACCESS_CODE_LENGTH and all(
        character in ACCESS_CODE_ALPHABET for character in normalized
    )


def format_access_code(code: str) -> str:
    normalized = normalize_access_code(code)
    if not is_valid_access_code(normalized):
        raise ValueError("Access codes must contain 12 valid characters.")
    return "-".join(
        normalized[index : index + ACCESS_CODE_GROUP_LENGTH]
        for index in range(0, ACCESS_CODE_LENGTH, ACCESS_CODE_GROUP_LENGTH)
    )


def access_code_digest(code: str, secret: str) -> str:
    normalized = normalize_access_code(code)
    return hmac.new(
        secret.encode("utf-8"),
        normalized.encode("ascii", errors="replace"),
        hashlib.sha256,
    ).hexdigest()
