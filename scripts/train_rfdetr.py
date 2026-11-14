"""Fine-tune RF-DETR Small on the SpaceNet COCO files from `make spacenet-data`.

Run it in the GPU detector image (see `make train-rfdetr`). Weights are written to
`<out>/checkpoint_best_total.pth`.
"""

import argparse

from rfdetr import RFDETRSmall


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="/data/spacenet/coco")
    p.add_argument("--out", default="/data/models/rfdetr-spacenet")
    p.add_argument("--epochs", type=int, default=20)
    p.add_argument("--batch-size", type=int, default=8)
    a = p.parse_args()
    model = RFDETRSmall()
    model.train(dataset_dir=a.data, output_dir=a.out, epochs=a.epochs, batch_size=a.batch_size,
                class_names=["building"], tensorboard=False)


if __name__ == "__main__":
    main()
