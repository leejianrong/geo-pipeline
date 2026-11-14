"""Export an RF-DETR model to ONNX for the torch-free detector (`make export-rfdetr`).

Writes `<out>/rfdetr-<variant>.onnx`. Without `--weights` it exports the COCO model; with a
fine-tuned checkpoint it exports that. Run it in the training image, which has `rfdetr[onnx]`.
"""

import argparse

import rfdetr


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--variant", choices=["Small", "Nano"], required=True)
    p.add_argument("--weights")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    cls = getattr(rfdetr, f"RFDETR{a.variant}")
    model = cls(device="cpu", **({"pretrain_weights": a.weights} if a.weights else {}))
    path = model.export(output_dir=a.out, verbose=False)
    print("wrote", path)


if __name__ == "__main__":
    main()
