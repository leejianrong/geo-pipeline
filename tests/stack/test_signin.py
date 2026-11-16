"""GEO-50: the web UI with sign-in turned on, against the real API.

The providers are faked (an httpx mock), everything else is real: the frontend ASGI app with
indah's secure sessions, and the API verifying the token the UI signs for the signed-in tenant.
"""

import asyncio
import importlib.util
import os
import sys

import httpx
import pytest
from starlette.testclient import TestClient

SECRET = os.environ.get("GEOPIPE_SESSION_SECRET", "dev-only-session-secret-change-it-0123456789")


def provider(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/login/oauth/access_token":
        return httpx.Response(200, json={"access_token": "tok"})
    return httpx.Response(200, json={"id": 4242, "login": "octocat", "name": "Octo Cat"})


@pytest.fixture
def ui(monkeypatch):
    os.environ.setdefault("API_URL", "http://api:8000")
    sys.path.insert(0, "/repo/stages/frontend")
    for k, v in {"GEOPIPE_GITHUB_CLIENT_ID": "gid", "GEOPIPE_GITHUB_CLIENT_SECRET": "gsecret",
                 "GEOPIPE_SESSION_SECRET": SECRET}.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("app_signin", "/repo/stages/frontend/app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SIGN_IN.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    return module


def test_the_ui_asks_for_a_login_then_acts_as_the_signed_in_tenant(ui):
    with TestClient(ui.app, follow_redirects=False) as c:
        assert c.get("/").headers["location"] == "/login"
        assert c.post("/api/session", json={}).status_code == 401
        state = httpx.URL(c.get("/auth/github/start").headers["location"]).params["state"]
        back = c.get("/auth/github/callback", params={"code": "good", "state": state})
        assert back.headers["location"] == "/"
        page = c.get("/")
        assert page.status_code == 200 and "geo-pipeline" in page.text
        session = c.post("/api/session", json={})
        assert session.status_code == 200 and session.json().get("token")

    client = ui.Client("github-4242", SECRET)  # what the session builds for that account

    async def call(method, path, **kw):
        return await client.request(method, path, **kw)

    assert asyncio.run(call("GET", "/jobs")).status_code == 200
    upload = asyncio.run(call("POST", "/uploads/dataset", json={"size": 10})).json()
    assert upload["key"].startswith("tenants/github-4242/uploads/datasets/")
    stranger = ui.Client("github-1", "w" * 40)  # a token signed with another secret
    assert asyncio.run(stranger.request("GET", "/jobs")).status_code == 401


def test_a_signed_in_session_shows_the_account_and_no_api_key_box(ui):
    from indah import Principal

    session = ui.build_session(Principal(id="github-4242", name="Octo Cat"))
    shown = " ".join(str(c.reactive_props().get("blocks", c.reactive_props().get(
                         "text", lambda: ""))())
                     for c in session._by_id.values() if c.type == "text")
    assert "Signed in as" in shown and "Octo Cat" in shown
    assert not [c for c in session._by_id.values()
                if c.type == "input" and "API key" in str(c.static_props())]
