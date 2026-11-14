import pytest
from fastapi.testclient import TestClient

from geopipe import store
from geopipe.api import create_app, parse_keys


class FakeRedis:
    def __init__(self, jobs=None):
        self.jobs = jobs or {}

    def hgetall(self, key):
        return self.jobs.get(key, {})

    def zrevrange(self, key, start, stop):
        return self.jobs.get(key, [])[start:stop + 1]

    def ping(self):
        return True


def client(jobs=None):
    s3 = store.client("http://public:8333")
    app = create_app(FakeRedis(jobs), s3, s3, "imagery", {"ka": "acme", "kb": "globex"})
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
