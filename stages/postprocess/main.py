"""Postprocess: collect per-tile boxes, georeference them, write GeoJSON, announce completion.

A background sweeper finalises jobs that stopped making progress as `incomplete`.
"""

import logging
import socket
import threading

from geopipe import bus, store
from geopipe.finalise import handle_detection, sweep_once
from geopipe.messages import DETECTIONS, Detection

log = logging.getLogger("postprocess")


def sweep_forever(r, s3, interval: float = 2.0) -> None:
    while True:
        try:
            sweep_once(r, s3)
        except Exception:
            log.exception("sweep failed")
        threading.Event().wait(interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r, s3 = bus.connect(), store.client()
    threading.Thread(target=sweep_forever, args=(r, s3), daemon=True).start()
    bus.consume(r, DETECTIONS, "postprocess", socket.gethostname(), Detection,
                lambda d: handle_detection(r, s3, d))
