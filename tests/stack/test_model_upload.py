"""S6: upload an ONNX model through a presigned URL, have the trainer check it, and run it."""

import json
import time

import numpy as np
import pytest
from conftest import BUCKET, build_tif
from onnx import TensorProto, helper, numpy_helper
from test_api import API, call
from test_user_model import constant_yolox, wait

from geopipe import bus, store
from geopipe.jobs import submit_job
from geopipe.messages import Options

A, B = "dev-key-a", "dev-key-b"
FORM = {"name": "My YOLOX", "decoder": "yolox", "classes": ["thing"], "licence": "MIT"}


def external_data_model() -> bytes:
    model = helper.make_model(
        helper.make_graph(
            [
                helper.make_node(
                    "Constant",
                    [],
                    ["o"],
                    value=numpy_helper.from_array(np.zeros((1, 84, 6), np.float32)),
                )
            ],
            "g",
            [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 64, 64])],
            [helper.make_tensor_value_info("o", TensorProto.FLOAT, [1, 84, 6])],
        ),
        opset_imports=[helper.make_opsetid("", 17)],
    )
    model.ir_version = 9
    weight = numpy_helper.from_array(np.zeros((2,), np.float32), "w")
    weight.data_location = TensorProto.EXTERNAL
    weight.external_data.add(key="location", value="../../etc/passwd")
    model.graph.initializer.append(weight)
    return model.SerializeToString()


def wrong_shape_model() -> bytes:
    model = helper.make_model(
        helper.make_graph(
            [
                helper.make_node(
                    "Constant",
                    [],
                    ["o"],
                    value=numpy_helper.from_array(np.zeros((1, 10, 6), np.float32)),
                )
            ],
            "g",
            [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 64, 64])],
            [helper.make_tensor_value_info("o", TensorProto.FLOAT, [1, 10, 6])],
        ),
        opset_imports=[helper.make_opsetid("", 17)],
    )
    model.ir_version = 9
    return model.SerializeToString()


@pytest.fixture(scope="module")
def raster(tmp_path_factory):
    """One small raster in the store that the jobs below run on."""
    s3 = store.client()
    store.ensure_bucket(s3, BUCKET)
    tif = tmp_path_factory.mktemp("mu") / "m.tif"
    build_tif(str(tif), 640, 640, [(100, 100, 50, 50)])
    s3.upload_file(str(tif), BUCKET, "uploaded-model.tif")
    return "uploaded-model.tif"


def run_with(model: str, key: str, timeout=90) -> str:
    r, s3 = bus.connect(), store.client()
    job = submit_job(r, s3, BUCKET, key, "acme", Options(model=f"user:{model}", overlap=0))
    return wait(r, job, {"done", "error"}, timeout)["state"]


@pytest.fixture(autouse=True)
def forget_models():
    yield
    bus.connect().delete("tenant:acme:models")  # other stack tests list this tenant's models


def upload_and_register(data: bytes, key=A, **form):
    _, body = call("POST", f"{API}/uploads/model", key, {"size": len(data)})
    up = json.loads(body)
    assert call("PUT", up["upload_url"], data=data)[0] == 200
    code, body = call("POST", f"{API}/models", key, {**FORM, **form, "upload_key": up["key"]})
    assert code == 202
    ids = json.loads(body)
    status = wait(bus.connect(), ids["job_id"], {"done", "error"})
    return ids, status


def test_a_good_model_is_registered_and_runs_a_job(raster):
    ids, status = upload_and_register(constant_yolox())
    assert status["state"] == "done" and status["input_size"] == "64"
    _, body = call("GET", f"{API}/models/{ids['model_id']}", A)
    model = json.loads(body)
    assert model["state"] == "ready" and model["input_size"] == 64 and model["scope"] == "own"
    assert model["classes"] == ["thing"] and model["licence"] == "MIT"
    assert call("GET", f"{API}/models/{ids['model_id']}", B)[0] == 404  # not shared

    assert run_with(ids["model_id"], raster) == "done"


@pytest.mark.parametrize(
    "data, expected",
    [
        (external_data_model(), "external data"),
        (wrong_shape_model(), "yolox decoder expects"),
        (b"definitely not an onnx file" * 5, "not a valid ONNX"),
    ],
)
def test_bad_models_fail_with_a_report_the_owner_can_read(raster, data, expected):
    ids, status = upload_and_register(data)
    assert status["state"] == "error" and expected in status["error"]
    _, body = call("GET", f"{API}/models/{ids['model_id']}", A)
    model = json.loads(body)
    assert model["state"] == "failed" and expected in model["report"]
    assert call("GET", f"{API}/models/{ids['model_id']}", B)[0] == 404
    assert run_with(ids["model_id"], raster, 60) == "error"  # a failed model cannot be used


def test_only_your_own_model_uploads_can_be_registered_and_the_form_is_checked():
    for key in (
        "tenants/globex/uploads/models/x.onnx",
        "tenants/acme/uploads/datasets/x.zip",
        "tenants/acme/uploads/models/../../x.onnx",
        "shared/models/x.onnx",
    ):
        code, _ = call("POST", f"{API}/models", A, {**FORM, "upload_key": key})
        assert code == 403, key
    ok = "tenants/acme/uploads/models/x.onnx"
    assert call("POST", f"{API}/models", A, {**FORM, "upload_key": ok, "classes": []})[0] == 422
    assert call("POST", f"{API}/models", A, {**FORM, "upload_key": ok, "decoder": "x"})[0] == 422
    assert call("POST", f"{API}/models", A, {**FORM, "upload_key": ok, "licence": ""})[0] == 422
    assert call("POST", f"{API}/uploads/model", None, {"size": 1})[0] == 401


def test_a_missing_upload_fails_at_once():
    key = "tenants/acme/uploads/models/not-there.onnx"
    code, body = call("POST", f"{API}/models", A, {**FORM, "upload_key": key})
    assert code == 202
    deadline, job_id = time.time() + 30, json.loads(body)["job_id"]
    status = wait(bus.connect(), job_id, {"error"}, 30)
    assert "upload not found" in status["error"] and time.time() < deadline


async def put_model(session, upload, name, data):
    import urllib.request

    from test_frontend import send

    await send(session, upload, "pick", {"filename": name, "size": len(data), "content_type": ""})
    url = upload.reactive_props()["target"]()["url"]
    req = urllib.request.Request(
        url.replace("localhost:8333", "seaweedfs:8333"),
        data=data,
        method="PUT",
        headers={"Host": "localhost:8333"},
    )
    assert urllib.request.urlopen(req, timeout=30).status == 200
    await send(session, upload, "done", {"filename": name, "size": len(data), "ok": True})


def shown(session):
    return "\n".join(
        str(t.reactive_props().get("blocks", t.reactive_props().get("text"))())
        for t in session._by_id.values()
        if t.type == "text"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("good", [True, False])
async def test_the_form_registers_a_model_or_shows_why_not(good):
    from test_frontend import labelled, new_session, send, sign_in

    session = new_session(f"mu-form-{good}")
    await sign_in(session, "dev-key-a")
    upload = labelled(session, "directupload", "Model ONNX")
    button = labelled(session, "button", "Register model")

    await send(session, button, "click")
    assert "Upload an ONNX file first" in shown(session)

    await put_model(session, upload, "m.onnx", constant_yolox() if good else wrong_shape_model())
    await send(session, button, "click")
    assert "needs a name, a licence" in shown(session)

    for label, text in (
        ("Model name", "Form model"),
        ("Model licence", "MIT"),
        ("Class names, comma separated", "thing"),
    ):
        await send(session, labelled(session, "textinput", label), "input", {"value": text})
    await send(session, button, "click")  # polls the trainer until it answers
    text = shown(session)
    if good:
        assert "Registered: a 64 px yolox model" in text
        options = labelled(session, "select", "Model").reactive_props()["options"]()
        assert any(o["label"] == "Form model (yours)" for o in options)  # in the Detect tab
    else:
        assert "was rejected" in text and "yolox decoder expects" in text
        options = labelled(session, "select", "Model").reactive_props()["options"]()
        assert not [o for o in options if o["value"].startswith("user:")]
