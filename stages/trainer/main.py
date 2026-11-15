"""Trainer: one worker for dataset ingest now, and training and benchmarks later (ADR-0016).

This small image has no torch. It handles `ingest`; a `train` message would need the GPU image.
"""

import logging
import socket

from geopipe import bus, store
from geopipe.ingest import ingest
from geopipe.messages import INGEST, IngestJob


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    r, s3 = bus.connect(), store.client()
    bus.consume(r, INGEST, "trainer", socket.gethostname(), IngestJob, lambda j: ingest(r, s3, j))


if __name__ == "__main__":
    main()
