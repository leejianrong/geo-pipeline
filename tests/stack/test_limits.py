"""S7: upload limits and quotas hold when the upload URL is issued, and the store holds the size."""

import json

import pytest
from test_api import API, call

from geopipe import bus

A = "dev-key-a"
GB = 1024**3


def test_the_store_refuses_a_file_of_another_size_than_the_one_declared():
    code, body = call("POST", f"{API}/uploads/dataset", A, {"size": 10})
    assert code == 200
    url = json.loads(body)["upload_url"]
    assert call("PUT", url, data=b"x" * 11)[0] == 403  # longer than declared
    assert call("PUT", url, data=b"x" * 5)[0] == 403  # shorter
    assert call("PUT", url, data=b"x" * 10)[0] == 200  # exactly as declared


def test_over_limit_uploads_are_refused_before_any_url_is_issued():
    code, body = call("POST", f"{API}/uploads/dataset", A, {"size": 5 * GB + 1})
    assert code == 413 and "at most 5120 MB" in json.loads(body)["detail"]
    assert call("POST", f"{API}/uploads/dataset", A, {"size": 5 * GB})[0] == 200
    code, body = call("POST", f"{API}/uploads/model", A, {"size": 501 * 1024**2})
    assert code == 413 and "at most 500 MB" in json.loads(body)["detail"]
    assert call("POST", f"{API}/uploads/model", A, {"size": 500 * 1024**2})[0] == 200


@pytest.fixture
def full_tenant():
    """Tenant globex holds as many datasets and models as the quota allows."""
    r = bus.connect()
    r.zadd("tenant:globex:datasets", {f"d{n}": n for n in range(20)})
    r.zadd("tenant:globex:models", {f"m{n}": n for n in range(20)})
    yield
    r.delete("tenant:globex:datasets", "tenant:globex:models")


def test_a_tenant_at_its_quota_gets_no_new_upload_url(full_tenant):
    for kind in ("dataset", "model"):
        code, body = call("POST", f"{API}/uploads/{kind}", "dev-key-b", {"size": 10})
        assert code == 409 and f"20 {kind}s" in json.loads(body)["detail"]
    assert call("POST", f"{API}/uploads/dataset", A, {"size": 10})[0] == 200  # another tenant


@pytest.mark.asyncio
async def test_the_train_tab_says_why_a_file_cannot_be_uploaded():
    from test_frontend import labelled, new_session, nodes, send, sign_in

    session = new_session("limits-ui")
    await sign_in(session, A)
    for label, big in (("Dataset zip", 6 * GB), ("Model ONNX", 600 * 1024**2)):
        upload = labelled(session, "directupload", label)
        with pytest.raises(RuntimeError):
            await send(
                session, upload, "pick", {"filename": "big", "size": big, "content_type": ""}
            )
        shown = [
            str(t.reactive_props()["text"]())
            for t in nodes(session, "text")
            if "text" in t.reactive_props()
        ]
        assert any("Cannot upload big" in t and "at most" in t for t in shown), label
        assert upload.reactive_props()["target"]() is None
