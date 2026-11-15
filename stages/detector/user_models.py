"""Models from the registry, loaded on demand (ADR-0017).

A job names one `user:<model id>`. The detector looks the id up for the job's own tenant (its own
models, then the shared ones), downloads the ONNX file and builds a backend for it, and keeps at
most `KEEP` backends: a fourth model evicts the least recently used. Another tenant's model id is
indistinguishable from an unknown one, so it fails the same way, and permanently.
"""

import logging
import tempfile
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from backends import Backend

from geopipe import bus, registry

log = logging.getLogger(__name__)
KEEP = 3


def build_backend(device: str, model: registry.ModelManifest, path: str) -> Backend:
    """An ONNX Runtime backend for a registered model, by the decoder its manifest names."""
    if model.decoder == "yolox":
        from yolox_onnx import load_yolox

        return load_yolox(device, path, model.classes)
    from rfdetr_onnx import load_rfdetr_onnx

    return load_rfdetr_onnx(device, path, dict(enumerate(model.classes)))


class UserModels:
    def __init__(self, s3, bucket: str, device: str, build: Callable = build_backend,
                 keep: int = KEEP, cache_dir: str | None = None) -> None:
        self.s3, self.bucket, self.device, self.build, self.keep = s3, bucket, device, build, keep
        self.dir = Path(cache_dir or tempfile.mkdtemp(prefix="user-models-"))
        self.loaded: OrderedDict[tuple[str, str], tuple[Backend, Path]] = OrderedDict()

    def get(self, tenant_id: str, model_id: str) -> Backend:
        hit = registry.find_model(self.s3, self.bucket, tenant_id, model_id)
        if hit is None or hit[1].state != "ready" or hit[1].backend is not None:
            raise bus.PermanentError(f"unknown model {model_id!r}")
        scope, model = hit
        key = (scope, model_id)
        if key in self.loaded:
            self.loaded.move_to_end(key)
            return self.loaded[key][0]
        path = self.dir / f"{scope}-{model_id}.onnx"
        try:
            onnx_key = f"{registry.model_prefix(scope, model_id)}/model.onnx"
            self.s3.download_file(self.bucket, onnx_key, str(path))
            backend = self.build(self.device, model, str(path))
        except Exception as e:  # a missing or broken file will not get better on retry
            path.unlink(missing_ok=True)
            raise bus.PermanentError(f"could not load model {model_id!r}: {e}") from e
        self.loaded[key] = (backend, path)
        while len(self.loaded) > self.keep:
            old, (_, old_path) = self.loaded.popitem(last=False)
            old_path.unlink(missing_ok=True)
            log.info("evicted model %s", old)
        return backend
