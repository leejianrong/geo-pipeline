"""Quantise a YOLOX ONNX model to INT8 (ORT static QDQ, per-channel weights).

Calibrates on training tiles from the target data, since activation ranges depend on the
imagery. Run it in the training image (`make quantise`). It checks the quantised model gives
the same output shape as the original and exits with an error if not.
"""

import argparse
import glob
import sys
import tempfile

import numpy as np
import onnxruntime as ort
from onnxruntime.quantization import CalibrationDataReader, QuantFormat, QuantType, quantize_static
from onnxruntime.quantization.shape_inference import quant_pre_process
from PIL import Image

sys.path.insert(0, "/app")  # stages/detector, mounted by `make quantise`
from yolox_onnx import letterbox  # noqa: E402


class Tiles(CalibrationDataReader):
    def __init__(self, files: list[str], input_name: str, size: tuple[int, int]):
        self.it, self.name, self.size = iter(files), input_name, size

    def get_next(self):
        f = next(self.it, None)
        if f is None:
            return None
        return {self.name: letterbox(np.array(Image.open(f).convert("RGB")), self.size)[0]}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--tiles", default="/data/spacenet/coco/train/*.png")
    p.add_argument("--count", type=int, default=30)
    a = p.parse_args()
    files = sorted(glob.glob(a.tiles))
    files = files[:: max(1, len(files) // a.count)][: a.count]
    inp = ort.InferenceSession(a.src, providers=["CPUExecutionProvider"]).get_inputs()[0]
    size = (inp.shape[2], inp.shape[3])
    with tempfile.TemporaryDirectory() as tmp:
        quant_pre_process(a.src, f"{tmp}/pre.onnx")
        quantize_static(f"{tmp}/pre.onnx", a.dst, Tiles(files, inp.name, size),
                        quant_format=QuantFormat.QDQ, activation_type=QuantType.QUInt8,
                        weight_type=QuantType.QInt8, per_channel=True)
    x = np.zeros((1, 3, *size), np.float32)
    def out_shape(model: str):
        sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
        return sess.run(None, {inp.name: x})[0].shape

    shapes = [out_shape(a.src), out_shape(a.dst)]
    if shapes[0] != shapes[1]:
        raise SystemExit(f"output shape changed: {shapes[0]} -> {shapes[1]}")
    print(f"wrote {a.dst} (calibrated on {len(files)} tiles)")


if __name__ == "__main__":
    main()
