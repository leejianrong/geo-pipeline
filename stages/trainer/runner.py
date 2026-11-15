"""The real training function of the GPU trainer image (GEO-68 and GEO-69 fill it in)."""

from geopipe import bus
from geopipe.messages import TrainJob
from geopipe.train import TrainContext


def run_training(job: TrainJob, ctx: TrainContext) -> str:
    raise bus.PermanentError("training itself is not implemented yet (GEO-68, GEO-69)")
