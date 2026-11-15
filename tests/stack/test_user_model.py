"""S5: a job names `user:<id>`; the detector loads that model from the registry, for that tenant."""

import json
import time

import numpy as np
import pytest
from conftest import BUCKET, build_tif
from onnx import TensorProto, helper, numpy_helper

from geopipe import bus, registry, store
from geopipe.jobs import submit_job
from geopipe.messages import Options


def constant_yolox(size: int = 64) -> bytes:
    """An ONNX model with a YOLOX-shaped output that never changes: one confident 16 px box in
    letterboxed pixels, centred at (20, 28). Stands in for a trained model, deterministically."""
    n = sum((size // s) ** 2 for s in (8, 16, 32))
    out = np.zeros((1, n, 6), np.float32)
    out[0, 3 * (size // 8) + 2] = [0.5, 0.5, np.log(2), np.log(2), 0.9, 0.9]
    node = helper.make_node("Constant", [], ["output"], value=numpy_helper.from_array(out))
    graph = helper.make_graph(
        [node],
        "constant",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, size, size])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, list(out.shape))],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9  # what the detector images' ONNX Runtime reads
    return model.SerializeToString()


@pytest.fixture(scope="module")
def registered(tmp_path_factory):
    r, s3 = bus.connect(), store.client()
    store.ensure_bucket(s3, BUCKET)
    tif = tmp_path_factory.mktemp("um") / "um.tif"
    build_tif(str(tif), 640, 640, [(100, 100, 50, 50)])  # one tile, with some data in it
    s3.upload_file(str(tif), BUCKET, "user-model.tif")
    registry.register_model(
        s3,
        r,
        BUCKET,
        registry.ModelManifest(
            id="constant-box",
            name="Constant box",
            decoder="yolox",
            input_size=64,
            classes=["thing"],
            licence="MIT",
            created_at=1.0,
        ),
        scope="acme",
    )
    s3.put_object(
        Bucket=BUCKET, Key="tenants/acme/models/constant-box/model.onnx", Body=constant_yolox()
    )
    yield r, s3
    r.delete("tenant:acme:models")


def wait(r, job_id, states, timeout=90):
    end = time.time() + timeout
    while r.hget(f"job:{job_id}:status", "state") not in states:
        assert time.time() < end, r.hgetall(f"job:{job_id}:status")
        time.sleep(0.5)
    return r.hgetall(f"job:{job_id}:status")


def test_user_model_each_model_in_the_registry_runs_a_job_for_its_owner(registered):
    r, s3 = registered
    job_id = submit_job(
        r, s3, BUCKET, "user-model.tif", "acme", Options(model="user:constant-box", overlap=0)
    )
    status = wait(r, job_id, {"done", "error"})
    assert status["state"] == "done", status
    bucket, key = status["output_uri"].removeprefix("s3://").split("/", 1)
    features = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())["features"]
    [feature] = features
    props = feature["properties"]
    assert props["class_name"] == "thing" and abs(props["score"] - 0.81) < 0.01
    # the model's box, centred at (20, 28) with size 16 in 64 px, scaled back to the 640 px tile
    assert [round(v) for v in props["pixel_bbox"]] == [120, 200, 280, 360]


def test_user_model_each_model_another_tenant_cannot_use_it(registered):
    r, s3 = registered
    job_id = submit_job(
        r, s3, BUCKET, "user-model.tif", "globex", Options(model="user:constant-box", overlap=0)
    )
    status = wait(r, job_id, {"done", "error"})
    assert status["state"] == "error" and "unknown model" in status["error"]


@pytest.mark.asyncio
async def test_user_model_each_model_chosen_in_the_detect_tab_runs_a_job(registered, tmp_path):
    from indah.components import UploadedFile
    from test_frontend import labelled, new_session, nodes, send, sign_in

    tif = tmp_path / "ui.tif"
    build_tif(str(tif), 640, 640, [(100, 100, 50, 50)])
    session = new_session("um-ui")
    await sign_in(session, "dev-key-a")
    select = labelled(session, "select", "Model")
    options = select.reactive_props()["options"]()
    mine = next(o for o in options if o["value"] == "user:constant-box")
    assert mine["label"] == "Constant box (yours)"
    assert "yolox" in [o["value"] for o in options]  # the built-ins are still there

    await send(session, select, "change", {"value": "user:constant-box"})
    files = [UploadedFile("ui.tif", "image/tiff", tif.read_bytes())]
    await send(session, nodes(session, "upload")[0], "upload", {"files": files})
    row = nodes(session, "table")[0].reactive_props()["rows"]()[0]
    assert row["state"] == "done" and row["boxes"] == 1  # the constant model's one box

    other = new_session("um-ui-b")
    await sign_in(other, "dev-key-b")
    other_options = labelled(other, "select", "Model").reactive_props()["options"]()
    assert not [o for o in other_options if o["value"].startswith("user:")]  # nothing of acme's
