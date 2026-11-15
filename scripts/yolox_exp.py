"""YOLOX experiment for SpaceNet buildings (one class), used by `make train-yolox`.

`YOLOX_VARIANT` (s, tiny) and `YOLOX_SIZE` (input pixels) pick the model; the shapes
are those of the official experiment files. Output goes to `<out>/yolox-spacenet[-variant-size]`.

YOLOX reads `<data_dir>/train2017`, `<data_dir>/val2017` and `<data_dir>/annotations/*.json`,
so this builds that layout from the COCO files of `geopipe spacenet-prepare` with symlinks.
"""

import json
import os
import shutil

from yolox.exp import Exp as BaseExp

SRC = os.environ.get("SPACENET_COCO", "/data/spacenet/coco")
# The trainer stage points these at any registered dataset (YOLOX_TRAIN, YOLOX_VALID: folders with
# images and `_annotations.coco.json`); `make train-yolox` leaves them unset and uses SRC.
TRAIN_DIR = os.environ.get("YOLOX_TRAIN", f"{SRC}/train")
VALID_DIR = os.environ.get("YOLOX_VALID", f"{SRC}/valid")
EPOCHS = int(os.environ.get("YOLOX_EPOCHS", "60"))
STAGE = "/tmp/yolox_data"
VARIANT = os.environ.get("YOLOX_VARIANT", "s")
SIZE = int(os.environ.get("YOLOX_SIZE", "640"))
SHAPES = {"s": (0.33, 0.50), "tiny": (0.33, 0.375)}  # depth, width


EXP_NAME = os.environ.get("YOLOX_EXP_NAME") or (
    "yolox-spacenet" + ("" if (VARIANT, SIZE) == ("s", 640) else f"-{VARIANT}-{SIZE}"))


class Exp(BaseExp):
    def __init__(self):
        super().__init__()
        self.depth, self.width = SHAPES[VARIANT]
        self.num_classes = int(os.environ.get("YOLOX_CLASSES", "1"))
        self.input_size = self.test_size = (SIZE, SIZE)
        self.random_size = (SIZE // 32 - 4, SIZE // 32 + 4)  # multi-scale range, in strides
        if VARIANT != "s":  # augmentation of the official tiny experiment
            self.mosaic_scale, self.enable_mixup = (0.5, 1.5), False
        # the last sixth of a run (10 of 60) trains without mosaic; evaluate about every 5 epochs
        self.max_epoch, self.eval_interval = EPOCHS, max(1, min(5, EPOCHS // 4))
        self.no_aug_epochs = min(10, EPOCHS // 6)
        self.data_dir, self.train_ann, self.val_ann = STAGE, "train.json", "valid.json"
        self.exp_name = EXP_NAME
        self.output_dir = os.environ.get("YOLOX_OUT", "/data/models")
        shutil.rmtree(STAGE, ignore_errors=True)
        os.makedirs(f"{STAGE}/annotations")
        for split, name, src in (
            ("train", "train2017", TRAIN_DIR), ("valid", "val2017", VALID_DIR)):
            os.symlink(src, f"{STAGE}/{name}")
            with open(f"{src}/_annotations.coco.json") as f:
                coco = json.load(f)
            with open(f"{STAGE}/annotations/{split}.json", "w") as f:
                json.dump(coco, f)
