"""The trainer's handler for a `TrainJob` (ADR-0016): checks, the per-tenant slot, the run.

The training itself is a seam, `TrainFn`: the GPU image passes the real one, tests pass a fake.
A trainer with no function (the small CPU image) fails every job at once with a message saying
which image is needed, so a user never waits for a run that cannot start.
"""

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import bus, registry
from .messages import TrainJob
from .presets import PRESETS

SLOT_TTL_S = 120  # the slot of a worker that died frees itself this long after its last refresh
SLOT_POLL_S = 1.0
NEEDS_GPU = ("training needs the GPU trainer image (`make up-gpu` on a machine with an NVIDIA "
             "GPU). This trainer is the small CPU image, which only validates datasets.")


@dataclass
class TrainContext:
    """What a training function may do besides train."""

    r: object
    job: TrainJob

    def progress(self, epoch: int, val_ap: float | None = None, **fields) -> None:
        """Record an epoch (and the validation AP, when there is one) in the run's status."""
        extra = {} if val_ap is None else {"val_ap": val_ap}
        bus.set_status(self.r, self.job.job_id, "running", epoch=epoch, **extra, **fields)
        bus.touch(self.r, self.job.job_id)


TrainFn = Callable[[TrainJob, TrainContext], str]  # returns the id of the registered model


def slot_key(tenant_id: str) -> str:
    return f"tenant:{tenant_id}:training"


@contextlib.contextmanager
def training_slot(r, tenant_id: str, run_id: str, poll: float = SLOT_POLL_S):
    """At most one training per tenant: wait for the slot, hold it (refreshed), then free it."""
    key = slot_key(tenant_id)
    while not r.set(key, run_id, nx=True, ex=SLOT_TTL_S):
        if r.get(key) == run_id:  # a retry of this very run, after a worker died
            r.expire(key, SLOT_TTL_S)
            break
        bus.set_status(r, run_id, "waiting", tenant_id=tenant_id, waiting_for=r.get(key) or "")
        time.sleep(poll)
    stop = threading.Event()

    def refresh() -> None:
        while not stop.wait(SLOT_TTL_S / 4):
            if r.get(key) == run_id:
                r.expire(key, SLOT_TTL_S)

    threading.Thread(target=refresh, daemon=True).start()
    try:
        yield
    finally:
        stop.set()
        if r.get(key) == run_id:
            r.delete(key)


def handle_train(r, s3, job: TrainJob, train_fn: TrainFn | None, *, poll: float = SLOT_POLL_S):
    run = registry.find_train_run(s3, job.bucket, job.tenant_id, job.job_id)
    if run is None:
        raise bus.PermanentError("unknown training run")
    try:
        if job.preset not in PRESETS:
            raise bus.PermanentError(f"unknown preset {job.preset!r}")
        hit = registry.find(s3, job.bucket, job.tenant_id, job.dataset_id)
        if not hit or hit[1].state != "ready":
            raise bus.PermanentError("the dataset is gone or is not ready")
        if train_fn is None:
            raise bus.PermanentError(NEEDS_GPU)
        with training_slot(r, job.tenant_id, job.job_id, poll):
            bus.set_status(r, job.job_id, "running", tenant_id=job.tenant_id, epoch=0)
            bus.touch(r, job.job_id)
            model_id = train_fn(job, TrainContext(r, job))
        registry.write_train_run(s3, r, job.bucket, job.tenant_id, run.model_copy(
            update={"state": "done", "model_id": model_id}))
        bus.set_status(r, job.job_id, "done", tenant_id=job.tenant_id, model_id=model_id)
    except bus.PermanentError as e:
        registry.write_train_run(s3, r, job.bucket, job.tenant_id, run.model_copy(
            update={"state": "failed", "error": str(e)}))
        raise
