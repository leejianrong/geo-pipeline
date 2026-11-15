"""Trainer: one worker for dataset ingest and training (and benchmarks later), ADR-0016.

The small CPU image has no torch and no training function: it validates datasets, and a `train`
message fails at once saying the GPU image is needed. The GPU image (`Dockerfile.gpu`,
`TRAINER_DEVICE=cuda`) also trains. Ingest never waits behind a training run, so each stream has
its own consumer thread.
"""

import logging
import os
import socket
import threading

from geopipe import bus, store
from geopipe.ingest import ingest
from geopipe.messages import INGEST, MODEL_INGEST, TRAIN, IngestJob, ModelIngestJob, TrainJob
from geopipe.model_ingest import ingest_model
from geopipe.runner import make_fake_train_fn, make_train_fn
from geopipe.train import handle_train


def training_function(s3):
    """The real training function on a GPU worker, None on the CPU image. `fake` is the test
    seam: a function that trains nothing, so CI can run the whole flow."""
    device = os.environ.get("TRAINER_DEVICE")
    if device == "fake":
        return make_fake_train_fn(s3)
    return make_train_fn(s3) if device == "cuda" else None


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    r, s3 = bus.connect(), store.client()
    me, train_fn = socket.gethostname(), training_function(s3)
    threading.Thread(target=bus.consume, daemon=True, args=(
        r, TRAIN, "trainer", me, TrainJob, lambda j: handle_train(r, s3, j, train_fn))).start()
    threading.Thread(target=bus.consume, daemon=True, args=(
        r, MODEL_INGEST, "trainer", me, ModelIngestJob, lambda j: ingest_model(r, s3, j))).start()
    bus.consume(r, INGEST, "trainer", me, IngestJob, lambda j: ingest(r, s3, j))


if __name__ == "__main__":
    main()
