from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.security import ADMIN_SESSION_SECONDS

_SALT = "venue-inventory-csrf"


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=_SALT)


def generate_csrf_token(secret_key: str, session_digest: str) -> str:
    return _serializer(secret_key).dumps({"sid": session_digest})


def csrf_token_is_valid(secret_key: str, token: str, session_digest: str) -> bool:
    if not token:
        return False
    try:
        payload = _serializer(secret_key).loads(token, max_age=ADMIN_SESSION_SECONDS)
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return False
    return payload.get("sid") == session_digest
