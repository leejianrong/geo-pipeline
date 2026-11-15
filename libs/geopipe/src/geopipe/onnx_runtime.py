"""ONNX Runtime sessions for the detector and the trainer: device choice and CPU threads."""

import os


def resolve_device(requested: str, cuda_available: bool) -> str:
    """`cpu` or `cuda`; asking for cuda without a GPU is an error, never a silent CPU run."""
    if requested not in ("cpu", "cuda"):
        raise SystemExit(f"DETECTOR_DEVICE must be cpu or cuda, got {requested!r}")
    if requested == "cuda" and not cuda_available:
        raise SystemExit("DETECTOR_DEVICE=cuda but no GPU is visible to this container "
                         "(use the GPU image and compose.gpu.yaml, with the NVIDIA runtime)")
    return requested


def thread_count(raw: str | None = None) -> int:
    """CPU threads per detector, from DETECTOR_THREADS (default 4: more was not faster)."""
    raw = os.environ.get("DETECTOR_THREADS", "4") if raw is None else raw
    if not raw.isdigit() or int(raw) < 1:
        raise SystemExit(f"DETECTOR_THREADS must be a positive integer, got {raw!r}")
    return int(raw)


def onnx_session(device: str, path: str):
    """An ONNX Runtime session on the requested device with `DETECTOR_THREADS` CPU threads."""
    import onnxruntime as ort  # only when an ONNX backend is enabled

    if device == "cuda":
        ort.preload_dlls()  # finds the CUDA libraries that the torch wheels ship
    providers = ["CPUExecutionProvider"]
    if resolve_device(device, "CUDAExecutionProvider" in ort.get_available_providers()) == "cuda":
        providers.insert(0, "CUDAExecutionProvider")
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = thread_count()
    session = ort.InferenceSession(path, opts, providers=providers)
    if device == "cuda" and session.get_providers()[0] != "CUDAExecutionProvider":
        raise SystemExit("onnxruntime could not load its CUDA provider (missing CUDA libraries?)")
    return session
