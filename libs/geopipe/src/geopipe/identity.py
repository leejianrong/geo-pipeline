"""Signed identity tokens (GEO-50, ADR-0019): who signed in, as a tenant, without a session store.

A token is `base64url(json payload) . base64url(HMAC-SHA256(secret, payload))` with an `exp`.
The frontend signs one for the login cookie and, per API call, a short one the API verifies, so
the API needs no knowledge of GitHub or Google, only the shared `GEOPIPE_SESSION_SECRET`.
"""

import base64
import hashlib
import hmac
import json
import re
import time

from .registry import SHARED

MIN_SECRET_BYTES = 32
TENANT = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def check_secret(secret: str) -> bytes:
    key = secret.encode()
    if len(key) < MIN_SECRET_BYTES:
        raise ValueError(f"GEOPIPE_SESSION_SECRET must be at least {MIN_SECRET_BYTES} bytes")
    return key


def sign(secret: str, payload: dict, ttl: float, now: float | None = None) -> str:
    body = _b64(json.dumps({**payload, "exp": (now or time.time()) + ttl},
                           separators=(",", ":")).encode())
    mac = hmac.new(check_secret(secret), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def verify(secret: str, token: str, now: float | None = None) -> dict | None:
    """The payload of a token we signed that has not expired, else None (never raises)."""
    try:
        body, mac = token.split(".")
        want = hmac.new(check_secret(secret), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _unb64(mac)):
            return None
        payload = json.loads(_unb64(body))
        return payload if isinstance(payload, dict) and payload["exp"] > (now or time.time()) \
            else None
    except (ValueError, KeyError, TypeError):
        return None


def tenant_for(provider: str, subject: str) -> str:
    """The tenant of a provider account: stable, lowercase and safe in S3 keys.
    `subject` is the provider's permanent id (GitHub's numeric id, Google's `sub`), never a name."""
    tenant = re.sub(r"[^a-z0-9_-]", "-", f"{provider}-{subject}".lower())[:64]
    if not TENANT.fullmatch(tenant) or tenant == SHARED:
        raise ValueError("not a usable account id")
    return tenant
