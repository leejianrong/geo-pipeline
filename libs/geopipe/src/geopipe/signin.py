"""Sign in with GitHub or Google in front of the web UI (GEO-50, ADR-0019).

`SignIn.wrap(app)` puts an OAuth 2.0 authorization-code login in front of any ASGI app: a visitor
with no valid login cookie is redirected to `/login` (or gets a 401 on `/api/` and a refused
WebSocket), and a successful login becomes a signed cookie naming the visitor's tenant. The app
behind it reads who is signed in with `login_of(connection)`. Needs `starlette` and `httpx`.

Secrets come from the environment and are never logged; the provider's own account id (not the
display name) becomes the tenant, so a renamed account keeps its data.
"""

import hmac
import html
import logging
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from starlette.applications import Starlette
from starlette.requests import HTTPConnection, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import identity

log = logging.getLogger("geopipe")
LOGIN_COOKIE, STATE_COOKIE = "geo_login", "geo_oauth_state"
LOGIN_TTL, STATE_TTL = 7 * 24 * 3600, 600
MESSAGES = {"denied": "Sign-in was cancelled.", "failed": "Sign-in did not work. Try again."}


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    profile_url: str
    scope: str

    def who(self, profile: Mapping) -> tuple[str, str]:
        """(permanent account id, display name) from the provider's profile."""
        if self.name == "github":
            return str(profile["id"]), profile.get("name") or profile.get("login") or ""
        return str(profile["sub"]), profile.get("name") or profile.get("email") or ""


def providers_from_env(env: Mapping[str, str] = os.environ) -> list[Provider]:
    """Configured providers, GitHub first. A provider needs both its id and its secret."""
    known = [
        ("github", "GitHub", "https://github.com/login/oauth/authorize",
         "https://github.com/login/oauth/access_token", "https://api.github.com/user",
         "read:user"),
        ("google", "Google", "https://accounts.google.com/o/oauth2/v2/auth",
         "https://oauth2.googleapis.com/token",
         "https://openidconnect.googleapis.com/v1/userinfo", "openid profile email")]
    found = []
    for name, label, authorize, token, profile, scope in known:
        cid = env.get(f"GEOPIPE_{name.upper()}_CLIENT_ID", "")
        secret = env.get(f"GEOPIPE_{name.upper()}_CLIENT_SECRET", "")
        if cid and secret:
            found.append(Provider(name, label, cid, secret, authorize, token, profile, scope))
    return found


class SignIn:
    def __init__(self, providers: list[Provider], secret: str, public_url: str,
                 http: httpx.AsyncClient | None = None) -> None:
        identity.check_secret(secret)
        if not providers:
            raise ValueError("no sign-in provider is configured")
        self.providers = {p.name: p for p in providers}
        self.secret = secret
        self.public_url = public_url.rstrip("/")
        self.secure = self.public_url.startswith("https://")
        self.http = http or httpx.AsyncClient(timeout=15)

    def login_of(self, conn: HTTPConnection) -> dict | None:
        """The signed-in visitor, `{"tenant", "name", "provider", "exp"}`, or None."""
        return identity.verify(self.secret, conn.cookies.get(LOGIN_COOKIE, ""))

    def _cookie(self, response: Response, name: str, value: str, max_age: int) -> None:
        response.set_cookie(name, value, max_age=max_age, httponly=True, samesite="lax",
                            secure=self.secure, path="/")

    async def login_page(self, request: Request) -> Response:
        note = MESSAGES.get(request.query_params.get("error", ""), "")
        buttons = "".join(
            f'<p><a class="b" href="/auth/{p.name}/start">Sign in with {html.escape(p.label)}'
            "</a></p>" for p in self.providers.values())
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>Sign in - geo-pipeline</title><style>body{font:16px system-ui;max-width:22rem;"
            "margin:4rem auto;padding:0 1rem}.b{display:block;padding:.7rem;border:1px solid #888;"
            "border-radius:.4rem;text-align:center;text-decoration:none;color:inherit}</style>"
            f"<h1>geo-pipeline</h1><p>{html.escape(note)}</p>{buttons}")

    async def start(self, request: Request) -> Response:
        p = self.providers.get(request.path_params["provider"])
        if p is None:
            return Response("unknown provider", 404)
        state = secrets.token_urlsafe(24)
        query = urlencode({"client_id": p.client_id, "redirect_uri": self._redirect_uri(p),
                           "response_type": "code", "scope": p.scope, "state": state})
        response = RedirectResponse(f"{p.authorize_url}?{query}")
        self._cookie(response, STATE_COOKIE,
                     identity.sign(self.secret, {"provider": p.name, "state": state}, STATE_TTL),
                     STATE_TTL)
        return response

    def _redirect_uri(self, p: Provider) -> str:
        return f"{self.public_url}/auth/{p.name}/callback"

    async def callback(self, request: Request) -> Response:
        p = self.providers.get(request.path_params["provider"])
        if p is None:
            return Response("unknown provider", 404)
        sent = identity.verify(self.secret, request.cookies.get(STATE_COOKIE, ""))
        state = request.query_params.get("state", "")
        if not sent or sent.get("provider") != p.name or not hmac.compare_digest(
                str(sent.get("state")), state):
            return Response("sign-in state did not match; start again at /login", 400)
        if request.query_params.get("error") or not request.query_params.get("code"):
            return RedirectResponse("/login?error=denied")
        try:
            subject, name = p.who(await self._profile(p, request.query_params["code"]))
            tenant = identity.tenant_for(p.name, subject)
        except Exception:  # never include the provider's reply: it may carry a token
            log.exception("sign-in with %s failed", p.name)
            return RedirectResponse("/login?error=failed")
        response = RedirectResponse("/")
        self._cookie(response, LOGIN_COOKIE, identity.sign(
            self.secret, {"tenant": tenant, "name": name, "provider": p.name}, LOGIN_TTL),
            LOGIN_TTL)
        response.delete_cookie(STATE_COOKIE, path="/")
        return response

    async def _profile(self, p: Provider, code: str) -> dict:
        resp = await self.http.post(p.token_url, headers={"Accept": "application/json"}, data={
            "client_id": p.client_id, "client_secret": p.client_secret, "code": code,
            "redirect_uri": self._redirect_uri(p), "grant_type": "authorization_code"})
        resp.raise_for_status()
        token = resp.json()["access_token"]
        resp = await self.http.get(p.profile_url, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json",
            "User-Agent": "geo-pipeline"})
        resp.raise_for_status()
        return resp.json()

    async def logout(self, request: Request) -> Response:
        response = RedirectResponse("/login")
        response.delete_cookie(LOGIN_COOKIE, path="/")
        return response

    def wrap(self, inner):
        """An ASGI app: the login routes, then `inner` for visitors who are signed in."""
        routes = Starlette(routes=[
            Route("/login", self.login_page), Route("/logout", self.logout),
            Route("/auth/{provider}/start", self.start),
            Route("/auth/{provider}/callback", self.callback)])

        async def app(scope, receive, send):
            if scope["type"] in ("http", "websocket"):
                path = scope["path"]
                if path in ("/login", "/logout") or path.startswith("/auth/"):
                    return await routes(scope, receive, send)
                if self.login_of(HTTPConnection(scope)) is None:
                    if scope["type"] == "websocket":
                        return await send({"type": "websocket.close", "code": 4401})
                    reply = (JSONResponse({"detail": "sign in first"}, 401)
                             if path.startswith("/api/") else RedirectResponse("/login"))
                    return await reply(scope, receive, send)
            await inner(scope, receive, send)

        return app
