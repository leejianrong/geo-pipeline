import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from geopipe import identity, signin

SECRET = "s" * 40
ENV = {"GEOPIPE_GITHUB_CLIENT_ID": "gid", "GEOPIPE_GITHUB_CLIENT_SECRET": "gsecret",
       "GEOPIPE_GOOGLE_CLIENT_ID": "oid", "GEOPIPE_GOOGLE_CLIENT_SECRET": "osecret"}


def fake_providers(request: httpx.Request) -> httpx.Response:
    if request.url.path in ("/login/oauth/access_token", "/token"):
        form = dict(p.split("=") for p in request.content.decode().split("&"))
        if form["code"] != "good":
            return httpx.Response(400, json={"error": "bad_verification_code"})
        return httpx.Response(200, json={"access_token": "tok-" + form["client_id"]})
    assert request.headers["Authorization"].startswith("Bearer tok-")
    if request.url.host == "api.github.com":
        return httpx.Response(200, json={"id": 4242, "login": "octocat", "name": None})
    return httpx.Response(200, json={"sub": "109", "name": "Ada", "email": "ada@example.com"})


def inner(request):
    return PlainTextResponse("app")


def make(providers=None):
    ps = providers or signin.providers_from_env(ENV)
    ps = [p.__class__(**{**p.__dict__, "token_url": p.token_url.replace(
        "oauth2.googleapis.com", "oauth.test"), }) for p in ps]
    s = signin.SignIn(ps, SECRET, "http://localhost:8080",
                      httpx.AsyncClient(transport=httpx.MockTransport(fake_providers)))
    app = Starlette(routes=[Route("/", inner), Route("/api/x", inner)])
    return s, TestClient(s.wrap(app), follow_redirects=False)


def test_providers_need_both_halves_and_github_comes_first():
    assert [p.name for p in signin.providers_from_env(ENV)] == ["github", "google"]
    only_id = {"GEOPIPE_GOOGLE_CLIENT_ID": "x"}
    assert signin.providers_from_env(only_id) == []
    assert [p.name for p in signin.providers_from_env(
        {k: v for k, v in ENV.items() if "GITHUB" not in k})] == ["google"]


def test_visitors_without_a_login_are_redirected_or_refused():
    _, c = make()
    r = c.get("/")
    assert r.status_code == 307 and r.headers["location"] == "/login"
    assert c.get("/api/x").status_code == 401
    page = c.get("/login").text
    assert page.index("GitHub") < page.index("Google")


def test_login_page_lists_only_configured_providers():
    _, c = make(signin.providers_from_env({k: v for k, v in ENV.items() if "GITHUB" in k}))
    assert "GitHub" in c.get("/login").text and "Google" not in c.get("/login").text
    assert c.get("/auth/google/start").status_code == 404


def sign_in(c, provider="github", code="good"):
    start = c.get(f"/auth/{provider}/start")
    assert start.status_code == 307
    query = httpx.URL(start.headers["location"]).params
    assert query["client_id"] and query["state"] and query["response_type"] == "code"
    return c.get(f"/auth/{provider}/callback", params={"code": code, "state": query["state"]})


@pytest.mark.parametrize("provider,tenant,name", [("github", "github-4242", "octocat"),
                                                   ("google", "google-109", "Ada")])
def test_a_good_login_sets_a_cookie_naming_the_tenant_and_opens_the_app(provider, tenant, name):
    s, c = make()
    r = sign_in(c, provider)
    assert r.status_code == 307 and r.headers["location"] == "/"
    login = identity.verify(SECRET, c.cookies["geo_login"])
    assert (login["tenant"], login["name"]) == (tenant, name)
    assert "httponly" in r.headers["set-cookie"].lower()
    assert c.get("/").text == "app" and c.get("/api/x").status_code == 200
    assert c.get("/logout").headers["location"] == "/login"
    assert c.get("/").status_code == 307  # the cookie is gone


def test_a_wrong_or_missing_state_is_refused_and_logs_nobody_in():
    _, c = make()
    c.get("/auth/github/start")
    assert c.get("/auth/github/callback", params={"code": "good", "state": "forged"}
                 ).status_code == 400
    assert c.get("/auth/github/callback", params={"code": "good"}).status_code == 400
    assert "geo_login" not in c.cookies


def test_state_from_one_provider_does_not_work_for_another():
    _, c = make()
    state = httpx.URL(c.get("/auth/github/start").headers["location"]).params["state"]
    assert c.get("/auth/google/callback", params={"code": "good", "state": state}
                 ).status_code == 400


def test_a_provider_error_or_denial_goes_back_to_login_without_a_cookie():
    _, c = make()
    r = sign_in(c, code="rejected")
    assert r.headers["location"] == "/login?error=failed" and "geo_login" not in c.cookies
    start = c.get("/auth/github/start")
    state = httpx.URL(start.headers["location"]).params["state"]
    r = c.get("/auth/github/callback", params={"error": "access_denied", "state": state})
    assert r.headers["location"] == "/login?error=denied"
    assert "cancelled" in c.get("/login?error=denied").text


def test_a_forged_login_cookie_is_not_a_login():
    _, c = make()
    c.cookies.set("geo_login", identity.sign("z" * 40, {"tenant": "github-1"}, 60))
    assert c.get("/").status_code == 307
