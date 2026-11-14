"""Fine-tune RF-DETR (Small or Nano) on the SpaceNet COCO files from `make spacenet-data`.

Run it in the GPU detector image (see `make train-rfdetr`). Weights are written to
`<out>/checkpoint_best_total.pth`.
"""

import argparse

import rfdetr


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="/data/spacenet/coco")
    p.add_argument("--variant", default="Small", choices=["Small", "Nano"])
    p.add_argument("--out")
    p.add_argument("--epochs", type=int, default=20)
    p.add_argument("--batch-size", type=int, default=8)
    a = p.parse_args()
    out = a.out or "/data/models/rfdetr-spacenet" + ("-nano" if a.variant == "Nano" else "")
    model = getattr(rfdetr, f"RFDETR{a.variant}")()
    model.train(dataset_dir=a.data, output_dir=out, epochs=a.epochs, batch_size=a.batch_size,
                class_names=["building"], tensorboard=False)


if __name__ == "__main__":
    main()
