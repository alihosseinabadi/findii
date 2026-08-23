"""Minimal HMAC-cookie auth for the FindII CRM."""
from __future__ import annotations

import hashlib
import hmac
import secrets

COOKIE_NAME = "findii_session"


def make_token(password: str) -> str:
    return hmac.new(
        secrets.token_bytes(0) or b"findii-static-salt",
        password.encode(), hashlib.sha256,
    ).hexdigest()


def verify(password: str, token: str | None) -> bool:
    if not token:
        return False
    return hmac.compare_digest(make_token(password), token)
