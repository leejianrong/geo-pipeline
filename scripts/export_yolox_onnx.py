"""Export a fine-tuned YOLOX checkpoint to ONNX with the raw head output (1, anchors, 5 + classes).

YOLOX's own exporter calls `torch.onnx._export`, which current torch no longer has. The decode
(grid offsets, NMS) stays outside the graph, in stages/detector/yolox_onnx.py.
"""

import argparse

import torch
from yolox.exp import get_exp


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("exp")
    p.add_argument("ckpt")
    p.add_argument("out")
    a = p.parse_args()
    exp = get_exp(a.exp, None)
    model = exp.get_model()
    model.load_state_dict(torch.load(a.ckpt, map_location="cpu", weights_only=False)["model"])
    model.eval()
    model.head.decode_in_inference = False
    torch.onnx.export(model, torch.zeros(1, 3, *exp.test_size), a.out, input_names=["images"],
                      output_names=["output"], opset_version=17, dynamo=False)


if __name__ == "__main__":
    main()
