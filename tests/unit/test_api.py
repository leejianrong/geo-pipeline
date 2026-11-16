import pytest
from fastapi.testclient import TestClient

from geopipe import store
from geopipe.api import DEFAULT_QUOTAS, Quotas, create_app, parse_keys


class FakeRedis:
    def __init__(self, jobs=None):
        self.jobs = jobs or {}

    def hgetall(self, key):
        return self.jobs.get(key, {})

    def zrevrange(self, key, start, stop):
        return self.jobs.get(key, [])[start:stop + 1]

    def zcard(self, key):
        return len(self.jobs.get(key, []))

    def ping(self):
        return True


class FakeHistory:
    """What `geopipe.history.History` answers: finished jobs, newest first, per tenant."""

    def __init__(self, rows):
        self.rows = rows  # [{"job_id", "tenant_id", ...}]

    def get(self, job_id, tenant_id):
        return next((r for r in self.rows if r["job_id"] == job_id
                     and r["tenant_id"] == tenant_id), None)

    def list(self, tenant_id, limit):
        return [r for r in self.rows if r["tenant_id"] == tenant_id][:limit]


def client(jobs=None, quotas=DEFAULT_QUOTAS, history=None):
    s3 = store.client("http://public:8333")
    app = create_app(FakeRedis(jobs), s3, s3, "imagery", {"ka": "acme", "kb": "globex"}, quotas,
                     history)
    return TestClient(app)


@pytest.fixture(autouse=True)
def creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "a")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "b")


A, B = {"X-API-Key": "ka"}, {"X-API-Key": "kb"}


def test_parse_keys():
    assert parse_keys("k1:t1, k2:t2") == {"k1": "t1", "k2": "t2"}
    with pytest.raises(ValueError):
        parse_keys("nocolon")


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_requires_a_valid_key(headers):
    c = client()
    assert c.post("/uploads", headers=headers).status_code == 401
    assert c.get("/jobs/x", headers=headers).status_code == 401


def test_upload_url_is_scoped_to_the_tenant():
    body = client().post("/uploads", headers=A).json()
    assert body["key"].startswith("tenants/acme/uploads/")
    assert body["upload_url"].startswith("http://public:8333/imagery/tenants/acme/uploads/")
    assert "X-Amz-Signature=" in body["upload_url"]  # v4: SeaweedFS rejects v2 presigned URLs


def test_dataset_upload_url_is_scoped_to_the_tenant_and_ends_in_zip():
    c = client()
    body = c.post("/uploads/dataset", json={"size": 10}, headers=A).json()
    assert body["key"].startswith("tenants/acme/uploads/datasets/") and body["key"].endswith(".zip")
    assert body["upload_url"].startswith("http://public:8333/imagery/tenants/acme/uploads/datasets/")
    assert c.post("/uploads/dataset", json={"size": 10}).status_code == 401
    other = c.post("/uploads/dataset", json={"size": 10}, headers=B).json()
    assert other["key"].startswith("tenants/globex/")


def test_cannot_submit_another_tenants_or_arbitrary_key():
    c = client()
    for key in ("tenants/globex/uploads/x.tif", "other/x.tif", "tenants/acme/../globex/x.tif"):
        assert c.post("/jobs", json={"key": key}, headers=A).status_code == 403


def test_job_request_uses_the_worker_option_model():
    r = client().post("/jobs", json={"key": "tenants/acme/uploads/x.tif",
                                     "options": {"overlap": "lots"}}, headers=A)
    assert r.status_code == 422


def test_other_tenants_job_looks_unknown():
    jobs = {"job:j1:status": {"state": "done", "tenant_id": "acme",
                              "output_uri": "s3://imagery/tenants/acme/results/j1.geojson"}}
    c = client(jobs)
    assert c.get("/jobs/j1", headers=A).json()["state"] == "done"
    assert c.get("/jobs/j1", headers=B).status_code == 404
    assert c.get("/jobs/j1/result", headers=B).status_code == 404
    assert c.get("/jobs/j1/result", headers=A).json()["url"].startswith(
        "http://public:8333/imagery/tenants/acme/results/j1.geojson")


def test_no_result_until_finished():
    c = client({"job:j2:status": {"state": "running", "tenant_id": "acme"}})
    assert c.get("/jobs/j2/result", headers=A).status_code == 409


def test_job_list_is_per_tenant_newest_first_and_skips_expired_statuses():
    jobs = {"tenant:acme:jobs": ["new", "gone", "old"], "tenant:globex:jobs": ["other"],
            "job:new:status": {"state": "done", "tenant_id": "acme"},
            "job:old:status": {"state": "error", "tenant_id": "acme"},
            "job:other:status": {"state": "done", "tenant_id": "globex"}}
    c = client(jobs)
    assert [j["job_id"] for j in c.get("/jobs", headers=A).json()] == ["new", "old"]
    assert [j["job_id"] for j in c.get("/jobs", headers=B).json()] == ["other"]
    assert c.get("/jobs").status_code == 401


def test_result_carries_the_preview_and_raster_size():
    status = {"state": "done", "tenant_id": "acme", "width": "650", "height": "480",
              "output_uri": "s3://imagery/tenants/acme/results/j1.geojson",
              "preview_uri": "s3://imagery/tenants/acme/results/j1.preview.jpg"}
    body = client({"job:j1:status": status}).get("/jobs/j1/result", headers=A).json()
    assert (body["width"], body["height"]) == (650, 480)
    assert "j1.preview.jpg" in body["preview_url"]


def test_a_dataset_or_model_upload_url_is_signed_for_the_declared_size():
    for path in ("/uploads/dataset", "/uploads/model"):
        url = client().post(path, json={"size": 1234}, headers=A).json()["upload_url"]
        assert "content-length" in url.lower()  # among the signed headers


def test_an_upload_over_the_size_limit_is_refused_before_a_url_is_issued():
    c = client(quotas=Quotas(dataset_bytes=1000, model_bytes=500))
    assert c.post("/uploads/dataset", json={"size": 1000}, headers=A).status_code == 200
    big = c.post("/uploads/dataset", json={"size": 1001}, headers=A)
    assert big.status_code == 413 and "at most" in big.json()["detail"]
    assert c.post("/uploads/model", json={"size": 501}, headers=A).status_code == 413
    assert c.post("/uploads/model", json={"size": 500}, headers=A).status_code == 200
    for bad in ({"size": 0}, {"size": -5}, {}, {"size": "big"}):
        assert c.post("/uploads/dataset", json=bad, headers=A).status_code == 422


def test_a_tenant_at_its_dataset_or_model_quota_gets_no_more_upload_urls():
    jobs = {"tenant:acme:datasets": ["d1", "d2"], "tenant:acme:models": ["m1"]}
    c = client(jobs, Quotas(datasets=2, models=2))
    full = c.post("/uploads/dataset", json={"size": 10}, headers=A)
    assert full.status_code == 409 and "delete one" in full.json()["detail"]
    assert c.post("/uploads/model", json={"size": 10}, headers=A).status_code == 200
    assert c.post("/uploads/dataset", json={"size": 10}, headers=B).status_code == 200  # globex


HISTORY = FakeHistory([
    {"job_id": "old", "tenant_id": "acme", "state": "done",
     "output_uri": "s3://imagery/tenants/acme/results/old.geojson"},
    {"job_id": "theirs", "tenant_id": "globex", "state": "done"}])


def test_a_job_valkey_forgot_is_answered_from_the_history_for_its_own_tenant_only():
    c = client(history=HISTORY)
    assert c.get("/jobs/old", headers=A).json()["state"] == "done"
    assert c.get("/jobs/old", headers=B).status_code == 404
    assert c.get("/jobs/old/result", headers=A).json()["url"].startswith(
        "http://public:8333/imagery/tenants/acme/results/old.geojson")
    assert client().get("/jobs/old", headers=A).status_code == 404  # no history: gone


def test_job_list_adds_history_rows_after_live_ones_without_duplicates():
    jobs = {"tenant:acme:jobs": ["new", "old"],
            "job:new:status": {"state": "running", "tenant_id": "acme"},
            "job:old:status": {"state": "done", "tenant_id": "acme"}}
    c = client(jobs, history=FakeHistory(HISTORY.rows + [
        {"job_id": "new", "tenant_id": "acme", "state": "done"},
        {"job_id": "ancient", "tenant_id": "acme", "state": "error"}]))
    got = c.get("/jobs", headers=A).json()
    assert [j["job_id"] for j in got] == ["new", "old", "ancient"]
    assert got[0]["state"] == "running"  # Valkey is the live truth
    assert [j["job_id"] for j in c.get("/jobs", headers=B).json()] == ["theirs"]


def test_a_signed_token_names_the_tenant_when_the_api_has_the_secret():
    from geopipe import identity

    secret = "k" * 40
    s3 = store.client("http://public:8333")
    c = TestClient(create_app(FakeRedis(), s3, s3, "imagery", {"ka": "acme"}, DEFAULT_QUOTAS,
                              session_secret=secret))

    def bearer(**claims):
        return {"Authorization": "Bearer " + identity.sign(secret, claims, ttl=60)}

    body = c.post("/uploads", headers=bearer(tenant="github-7")).json()
    assert body["key"].startswith("tenants/github-7/uploads/")
    for bad in (bearer(tenant="shared"), bearer(tenant="../x"), bearer(), bearer(tenant=5),
                {"Authorization": "Bearer " + identity.sign("j" * 40, {"tenant": "x"}, 60)},
                {"Authorization": "Bearer garbage"}):
        assert c.post("/uploads", headers=bad).status_code == 401
    assert c.post("/uploads", headers=A).status_code == 200  # API keys still work
    plain = client()  # no secret configured: a token is just a bad credential
    assert plain.post("/uploads", headers=bearer(tenant="github-7")).status_code == 401
