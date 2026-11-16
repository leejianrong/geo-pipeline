"""What every tab of the UI shares: the API client, the signed-in state, and fetching.

The UI is a client of the HTTP API only (V6): it holds no Valkey or S3 credentials. File bytes
go browser -> this server -> presigned URL; this server is the only thing that talks to the
API, so the browser never sees the key's presigned URLs.
"""

import asyncio
import base64
import inspect
import os

import httpx
import indah
from indah import Signal

from geopipe import identity

API = os.environ.get("API_URL", "http://api:8000")
# Presigned URLs are signed for the address browsers use ("public"); inside compose this
# server reaches the store by another name. Set "public=internal"; unset when they are equal.
REWRITE = os.environ.get("S3_REWRITE", "")


def reach(url: str) -> tuple[str, dict]:
    if REWRITE and (pair := REWRITE.split("=", 1))[0] in url:
        return url.replace(pair[0], pair[1]), {"Host": pair[0]}
    return url, {}


async def fetch(url: str) -> bytes:
    target, headers = reach(url)
    async with httpx.AsyncClient(timeout=120) as c:
        resp = await c.get(target, headers=headers)
        resp.raise_for_status()
        return resp.content


def data_uri(content: bytes, media_type: str) -> str:
    return f"data:{media_type};base64," + base64.b64encode(content).decode()


class Client:
    """One viewer's credentials and sign-in state, shared by the tabs of their session.

    Signed in with GitHub or Google (`tenant` and the shared `secret`), every call carries a
    short-lived signed token for that tenant (ADR-0019); otherwise the viewer types an API key."""

    def __init__(self, tenant: str | None = None, secret: str = "") -> None:
        self.key = Signal("")
        self.signed_in = Signal(tenant is not None)
        self.tenant, self.secret = tenant, secret

    def headers(self) -> dict:
        if self.tenant:
            token = identity.sign(self.secret, {"tenant": self.tenant}, ttl=300)
            return {"Authorization": f"Bearer {token}"}
        return {"X-API-Key": self.key.value}

    async def request(self, method: str, path: str, **kw) -> httpx.Response:
        async with httpx.AsyncClient(timeout=60) as c:
            return await c.request(method, f"{API}{path}", headers=self.headers(), **kw)


async def wait_job(client: "Client", job_id: str, interval: float = 1.0,
                   give_up: float = 1800.0) -> dict | None:
    """Poll a job until it is done or failed; None if it takes longer than `give_up` seconds."""
    spent = 0.0
    while spent < give_up:
        await asyncio.sleep(interval)
        spent += interval
        status = (await client.request("GET", f"/jobs/{job_id}")).json()
        if status["state"] in ("done", "error"):
            return status
    return None


class _OnChange:
    """Mixin for indah inputs: run `on_change` (sync or async) after the viewer picks a value."""

    def __init__(self, *args, on_change=None, **kw) -> None:
        super().__init__(*args, **kw)
        self._on_change = on_change

    def handle(self, event: str, payload: dict):
        handled = super().handle(event, payload)
        if handled and self._on_change is not None and event in ("change", "input", "select"):
            result = self._on_change()
            if inspect.iscoroutine(result):
                return result  # the session awaits it in the background
        return handled


class Select(_OnChange, indah.Select):
    pass


class Segmented(_OnChange, indah.Segmented):
    pass


class Table(_OnChange, indah.Table):
    pass
