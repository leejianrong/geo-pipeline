"""YOLOX-s experiment for SpaceNet buildings (one class), used by `make train-yolox`.

YOLOX reads `<data_dir>/train2017`, `<data_dir>/val2017` and `<data_dir>/annotations/*.json`,
so this builds that layout from the COCO files of `geopipe spacenet-prepare` with symlinks.
"""

import json
import os
import shutil

from yolox.exp import Exp as BaseExp

SRC = os.environ.get("SPACENET_COCO", "/data/spacenet/coco")
STAGE = "/tmp/yolox_data"


class Exp(BaseExp):
    def __init__(self):
        super().__init__()
        self.depth, self.width, self.num_classes = 0.33, 0.50, 1  # YOLOX-s
        self.max_epoch, self.no_aug_epochs, self.eval_interval = 60, 10, 5
        self.data_dir, self.train_ann, self.val_ann = STAGE, "train.json", "valid.json"
        self.exp_name = "yolox-spacenet"
        self.output_dir = os.environ.get("YOLOX_OUT", "/data/models")
        shutil.rmtree(STAGE, ignore_errors=True)
        os.makedirs(f"{STAGE}/annotations")
        for split, name in (("train", "train2017"), ("valid", "val2017")):
            os.symlink(f"{SRC}/{split}", f"{STAGE}/{name}")
            with open(f"{SRC}/{split}/_annotations.coco.json") as f:
                coco = json.load(f)
            with open(f"{STAGE}/annotations/{split}.json", "w") as f:
                json.dump(coco, f)
