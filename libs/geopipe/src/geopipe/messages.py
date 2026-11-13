"""Versioned wire contracts between stages. Unknown schema versions are rejected."""

from typing import Literal

from pydantic import BaseModel

SCHEMA_VERSION = 1
JOBS_IN, TILES, DETECTIONS, JOBS_DONE = "jobs:in", "tiles", "detections", "jobs:done"


class Window(BaseModel):
    """Pixel window in the source raster."""

    x_off: int
    y_off: int
    w: int
    h: int


class Options(BaseModel):
    model: str = "stub"
    tile_size: int = 640
    overlap: float = 0.2
    conf: float = 0.25
    iou: float = 0.5


class Box(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float
    score: float
    class_id: int
    class_name: str


class _Msg(BaseModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    tenant_id: str
    job_id: str


class Job(_Msg):
    bucket: str
    key: str
    options: Options = Options()


class Tile(_Msg):
    idx: int
    total: int
    window: Window
    tile_bucket: str
    tile_key: str
    options: Options


class Detection(_Msg):
    idx: int
    total: int
    window: Window
    boxes: list[Box]


class Done(_Msg):
    status: Literal["done", "error"]
    output_uri: str | None = None
    error: str | None = None
