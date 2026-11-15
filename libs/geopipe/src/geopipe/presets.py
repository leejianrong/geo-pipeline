"""Training presets (ADR-0016): a name maps to a base model and the recipe of ADR-0014's
benchmarks. Raw hyperparameters are not exposed."""

from dataclasses import dataclass

MAX_EPOCHS = 200


@dataclass(frozen=True)
class Preset:
    decoder: str  # "yolox" or "rfdetr": how the exported model's output is read
    variant: str  # YOLOX: s, tiny. RF-DETR: Small, Nano
    input_size: int
    epochs: int  # the default; the user may pick 1 to MAX_EPOCHS
    label: str


PRESETS = {
    "fast": Preset("yolox", "tiny", 416, 60, "YOLOX-tiny 416"),
    "balanced": Preset("yolox", "s", 640, 60, "YOLOX-s 640"),
    "accurate": Preset("rfdetr", "Nano", 384, 30, "RF-DETR Nano"),
    "best": Preset("rfdetr", "Small", 512, 30, "RF-DETR Small"),
}
