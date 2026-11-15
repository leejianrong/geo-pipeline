"""Check an uploaded ONNX model before it is registered (ADR-0017).

An uploaded file is untrusted. ONNX is data, not code, but it can point at external data files by
path and can name operators from custom libraries, so the checks are: it parses; no tensor lives in
an external file; only the standard operator domains; static square input; output shapes that
match the declared decoder and class count (read from the graph and then confirmed by running
the model once). The session is built from the bytes, never from a path.
"""

from .dataset_zip import ProblemsFound

MAX_BYTES = 500 * 1024**2
DECODERS = ("yolox", "rfdetr")
STANDARD_DOMAINS = {"", "ai.onnx", "ai.onnx.ml"}
YOLOX_STRIDES = (8, 16, 32)
RFDETR_QUERIES = 300  # what geopipe/rfdetr_onnx.py selects from


def yolox_anchors(size: int) -> int:
    return sum((size // s) ** 2 for s in YOLOX_STRIDES)


def _dims(value) -> list:
    return [d.dim_value if d.HasField("dim_value") else None
            for d in value.type.tensor_type.shape.dim]


def _external(tensor) -> bool:
    return tensor.data_location == 1  # TensorProto.EXTERNAL


def validate_onnx(data: bytes, decoder: str, classes: list[str],
                  max_bytes: int = MAX_BYTES) -> int:
    """Return the model's input size in pixels, or raise `ProblemsFound` saying what is wrong."""
    if decoder not in DECODERS:
        raise ProblemsFound([f"the decoder must be one of {', '.join(DECODERS)}"])
    if not classes:
        raise ProblemsFound(["name at least one class"])
    if len(data) > max_bytes:
        raise ProblemsFound([f"the file is {len(data) / 1024**2:.0f} MB; the limit is "
                             f"{max_bytes / 1024**2:.0f} MB"])
    import onnx

    try:
        model = onnx.load_from_string(data)
    except Exception as e:
        raise ProblemsFound([f"not a valid ONNX file ({type(e).__name__})"]) from e
    graph = model.graph
    if not graph.node:
        raise ProblemsFound(["the ONNX graph has no operators"])
    tensors = [*graph.initializer, *(a.t for n in graph.node for a in n.attribute
                                     if a.HasField("t"))]
    if any(_external(t) for t in tensors):
        raise ProblemsFound(["the model keeps weights in external data files, which are not "
                             "accepted; export it as one self-contained file"])
    domains = {n.domain for n in graph.node} - STANDARD_DOMAINS
    if domains:
        raise ProblemsFound([f"operators from custom domains are not accepted: "
                             f"{', '.join(sorted(domains))}"])
    inputs = [i for i in graph.input if i.name not in {t.name for t in graph.initializer}]
    if len(inputs) != 1:
        raise ProblemsFound([f"the model must have one image input, it has {len(inputs)}"])
    shape = _dims(inputs[0])
    if len(shape) != 4 or shape[:2] != [1, 3] or None in shape or shape[2] != shape[3]:
        raise ProblemsFound([f"the input must be a fixed 1 x 3 x S x S image, got {shape}"])
    size = shape[2]
    problems = _output_problems([_dims(o) for o in graph.output], decoder, size, len(classes))
    if problems:
        raise ProblemsFound(problems)
    problems = _run_problems(data, inputs[0].name, size, decoder, len(classes))
    if problems:
        raise ProblemsFound(problems)
    return size


def _output_problems(outputs: list[list], decoder: str, size: int, n: int) -> list[str]:
    if decoder == "yolox":
        want = [[1, yolox_anchors(size), 5 + n]]
        say = (f"{want[0]} for {size} px input and {n} class(es): 1 x anchors x (5 + classes)")
    else:
        want = [[1, RFDETR_QUERIES, 4], [1, RFDETR_QUERIES, n + 1]]
        say = (f"{want[0]} and {want[1]} for {n} class(es): boxes, then one logit per class "
               "plus one")
    if outputs != want:
        return [f"the {decoder} decoder expects output shapes {say}; the model has {outputs}"]
    return []


def _run_problems(data: bytes, input_name: str, size: int, decoder: str, n: int) -> list[str]:
    """Build the session from the bytes and run it once: the graph's claims must be true."""
    import numpy as np
    import onnxruntime as ort

    try:
        session = ort.InferenceSession(data, providers=["CPUExecutionProvider"])
        outs = session.run(None, {input_name: np.zeros((1, 3, size, size), np.float32)})
    except Exception as e:
        return [f"the model could not be run: {str(e).splitlines()[0][:200]}"]
    problems = _output_problems([list(o.shape) for o in outs], decoder, size, n)
    return [f"the model's real outputs differ from what it declares. {p}" for p in problems]
