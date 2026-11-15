import sys
from pathlib import Path

import pytest
from fakes import FakeS3, FakeValkey

sys.path.insert(0, str(Path(__file__).parents[2] / "stages" / "detector"))
from user_models import UserModels  # noqa: E402

from geopipe import bus, registry  # noqa: E402

BUCKET = "imagery"


class Store(FakeS3):
    def download_file(self, Bucket, Key, Filename):  # noqa: N803
        Path(Filename).write_bytes(self.get_object(Bucket, Key)["Body"].read())


def model(model_id, **kw):
    return registry.ModelManifest(
        id=model_id,
        name=model_id,
        decoder="yolox",
        input_size=64,
        classes=["thing"],
        licence="MIT",
        created_at=1.0,
        **kw,
    )


@pytest.fixture
def world(tmp_path):
    s3, r = Store(), FakeValkey()
    for scope, ids in (("acme", ["a1", "a2", "a3", "a4"]), ("globex", ["g1"]), ("shared", ["s1"])):
        for model_id in ids:
            registry.register_model(s3, r, BUCKET, model(model_id), scope=scope)
            s3.put_object(
                Bucket=BUCKET,
                Key=f"{registry.model_prefix(scope, model_id)}/model.onnx",
                Body=model_id.encode(),
            )
    built = []

    def build(device, manifest, path):
        built.append(manifest.id)
        return lambda img, conf: [manifest.id]

    return UserModels(s3, BUCKET, "cpu", build=build, cache_dir=str(tmp_path)), built, tmp_path


def test_a_tenant_loads_its_own_model_and_a_shared_one(world):
    models, built, _ = world
    assert models.get("acme", "a1")(None, 0.1) == ["a1"]
    assert models.get("acme", "s1")(None, 0.1) == ["s1"]
    assert built == ["a1", "s1"]


def test_another_tenants_model_fails_permanently_like_an_unknown_one(world):
    models, built, _ = world
    for tenant, name in (("acme", "g1"), ("globex", "a1"), ("acme", "nope"), ("acme", "../x")):
        with pytest.raises(bus.PermanentError, match="unknown model"):
            models.get(tenant, name)
    assert built == []


def test_a_fourth_model_evicts_the_least_recently_used(world):
    models, built, tmp = world
    for name in ("a1", "a2", "a3"):
        models.get("acme", name)
    models.get("acme", "a1")  # a2 is now the oldest
    models.get("acme", "a4")
    assert [k[1] for k in models.loaded] == ["a3", "a1", "a4"]
    assert not (tmp / "acme-a2.onnx").exists() and (tmp / "acme-a1.onnx").exists()
    models.get("acme", "a2")  # loads again, evicting a3
    assert built == ["a1", "a2", "a3", "a4", "a2"]
    assert [k[1] for k in models.loaded] == ["a1", "a4", "a2"]


def test_a_cached_model_is_not_rebuilt_or_redownloaded(world):
    models, built, _ = world
    first = models.get("acme", "a1")
    assert models.get("acme", "a1") is first and built == ["a1"]


def test_a_broken_file_is_a_permanent_error_and_leaves_nothing_behind(world):
    models, _, tmp = world

    def broken(device, manifest, path):
        raise ValueError("not an onnx file")

    models.build = broken
    with pytest.raises(bus.PermanentError, match="could not load model 'a1': not an onnx file"):
        models.get("acme", "a1")
    assert not list(tmp.glob("*.onnx")) and not models.loaded


def test_a_missing_file_is_a_permanent_error(world):
    models, _, _ = world
    registry.register_model(models.s3, FakeValkey(), BUCKET, model("empty"), scope="acme")
    with pytest.raises(bus.PermanentError, match="could not load model 'empty'"):
        models.get("acme", "empty")


def test_a_built_in_registry_model_has_no_onnx_to_load(world):
    models, _, _ = world
    registry.register_model(
        models.s3,
        FakeValkey(),
        BUCKET,
        model("builtin", backend="yolox-tiny-buildings"),
        scope="shared",
    )
    with pytest.raises(bus.PermanentError, match="unknown model"):
        models.get("acme", "builtin")
