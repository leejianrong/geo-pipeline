import pytest

from geopipe import identity

SECRET = "x" * 40


def test_a_signed_token_verifies_until_it_expires():
    token = identity.sign(SECRET, {"tenant": "github-1"}, ttl=60, now=1000)
    assert identity.verify(SECRET, token, now=1030)["tenant"] == "github-1"
    assert identity.verify(SECRET, token, now=1061) is None


def test_tampered_foreign_and_malformed_tokens_are_nobody():
    token = identity.sign(SECRET, {"tenant": "github-1"}, ttl=60)
    body, mac = token.split(".")
    forged = identity.sign(SECRET, {"tenant": "github-2"}, ttl=60).split(".")[0]
    for bad in (f"{forged}.{mac}", token + "x", body, "", "a.b", identity.sign("y" * 40, {}, 60)):
        assert identity.verify(SECRET, bad) is None


def test_a_short_secret_is_refused():
    with pytest.raises(ValueError):
        identity.sign("short", {}, 60)
    assert identity.verify("short", "a.b") is None


def test_tenant_ids_are_stable_safe_and_never_the_shared_namespace():
    assert identity.tenant_for("github", "12345") == "github-12345"
    assert identity.tenant_for("google", "1A/B../c") == "google-1a-b---c"
    assert identity.TENANT.fullmatch(identity.tenant_for("google", "x" * 200))
