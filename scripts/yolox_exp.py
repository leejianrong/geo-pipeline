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
STAGE = "/tmp/yolox_data"
VARIANT = os.environ.get("YOLOX_VARIANT", "s")
SIZE = int(os.environ.get("YOLOX_SIZE", "640"))
SHAPES = {"s": (0.33, 0.50), "tiny": (0.33, 0.375)}  # depth, width


EXP_NAME = "yolox-spacenet" + ("" if (VARIANT, SIZE) == ("s", 640) else f"-{VARIANT}-{SIZE}")


class Exp(BaseExp):
    def __init__(self):
        super().__init__()
        self.depth, self.width = SHAPES[VARIANT]
        self.num_classes = 1
        self.input_size = self.test_size = (SIZE, SIZE)
        self.random_size = (SIZE // 32 - 4, SIZE // 32 + 4)  # multi-scale range, in strides
        if VARIANT != "s":  # augmentation of the official tiny experiment
            self.mosaic_scale, self.enable_mixup = (0.5, 1.5), False
        self.max_epoch, self.no_aug_epochs, self.eval_interval = 60, 10, 5
        self.data_dir, self.train_ann, self.val_ann = STAGE, "train.json", "valid.json"
        self.exp_name = EXP_NAME
        self.output_dir = os.environ.get("YOLOX_OUT", "/data/models")
        shutil.rmtree(STAGE, ignore_errors=True)
        os.makedirs(f"{STAGE}/annotations")
        for split, name in (("train", "train2017"), ("valid", "val2017")):
            os.symlink(f"{SRC}/{split}", f"{STAGE}/{name}")
            with open(f"{SRC}/{split}/_annotations.coco.json") as f:
                coco = json.load(f)
            with open(f"{STAGE}/annotations/{split}.json", "w") as f:
                json.dump(coco, f)
