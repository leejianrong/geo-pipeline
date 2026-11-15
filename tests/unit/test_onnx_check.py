import numpy as np
import pytest
from onnx import TensorProto, helper, numpy_helper

from geopipe.dataset_zip import ProblemsFound
from geopipe.onnx_check import validate_onnx, yolox_anchors


def make_model(outputs, size=64, in_shape=None, domain="", external=False, extra_nodes=()):
    """A model whose outputs are constants of the given shapes (declared and real agree)."""
    nodes, infos = [], []
    for k, shape in enumerate(outputs):
        value = numpy_helper.from_array(np.zeros(shape, np.float32))
        nodes.append(helper.make_node("Constant", [], [f"out{k}"], value=value, domain=domain))
        infos.append(helper.make_tensor_value_info(f"out{k}", TensorProto.FLOAT, shape))
    graph = helper.make_graph(
        [*nodes, *extra_nodes],
        "g",
        [
            helper.make_tensor_value_info(
                "images", TensorProto.FLOAT, in_shape or [1, 3, size, size]
            )
        ],
        infos,
    )
    if external:
        weight = numpy_helper.from_array(np.zeros((2,), np.float32), "w")
        weight.data_location = TensorProto.EXTERNAL
        weight.external_data.add(key="location", value="../../etc/passwd")
        graph.initializer.append(weight)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    return model.SerializeToString()


def yolox(n_classes=1, size=64):
    return make_model([[1, yolox_anchors(size), 5 + n_classes]], size)


def rfdetr(n_classes=1, size=64):
    return make_model([[1, 300, 4], [1, 300, n_classes + 1]], size)


def problems(data, decoder="yolox", classes=("a",), **kw):
    with pytest.raises(ProblemsFound) as e:
        validate_onnx(data, decoder, list(classes), **kw)
    return "\n".join(e.value.problems)


def test_good_models_pass_and_report_their_input_size():
    assert validate_onnx(yolox(), "yolox", ["a"]) == 64
    assert validate_onnx(yolox(3, 96), "yolox", ["a", "b", "c"]) == 96
    assert validate_onnx(rfdetr(2), "rfdetr", ["a", "b"]) == 64


def test_the_wrong_output_shape_for_the_decoder_is_named():
    text = problems(yolox(n_classes=2), classes=["a"])  # two classes in the model, one declared
    assert "yolox decoder expects" in text and "the model has [[1, 84, 7]]" in text
    assert "rfdetr decoder expects" in problems(yolox(), decoder="rfdetr")
    assert "yolox decoder expects" in problems(rfdetr(), decoder="yolox")
    assert "yolox decoder expects" in problems(make_model([[1, 10, 6]]))


def test_a_model_that_keeps_weights_in_external_files_is_refused():
    text = problems(make_model([[1, yolox_anchors(64), 6]], external=True))
    assert "external data" in text


def test_operators_from_custom_domains_are_refused():
    assert "custom domains" in problems(make_model([[1, yolox_anchors(64), 6]], domain="my.ops"))


def test_unparsable_empty_and_oversized_files_are_refused():
    assert "not a valid ONNX" in problems(b"\x00this is not protobuf\xff" * 10)
    assert "no operators" in problems(b"")
    assert "limit is" in problems(yolox(), max_bytes=100)


def test_the_input_must_be_a_fixed_square_image():
    assert "fixed 1 x 3 x S x S" in problems(make_model([[1, 84, 6]], in_shape=[1, 3, 64, 32]))
    assert "fixed 1 x 3 x S x S" in problems(make_model([[1, 84, 6]], in_shape=[1, 1, 64, 64]))
    assert "fixed 1 x 3 x S x S" in problems(make_model([[1, 84, 6]], in_shape=["n", 3, 64, 64]))


def test_declared_decoder_and_classes_are_checked_first():
    assert "decoder must be one of" in problems(yolox(), decoder="detr")
    assert "at least one class" in problems(yolox(), classes=[])


def test_a_model_whose_real_output_differs_from_its_declaration_is_refused():
    """The graph says (1, 84, 6) but the constant it returns is (1, 10, 6)."""
    liar = helper.make_node(
        "Constant", [], ["out0"], value=numpy_helper.from_array(np.zeros((1, 10, 6), np.float32))
    )
    graph = helper.make_graph(
        [liar],
        "g",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 64, 64])],
        [helper.make_tensor_value_info("out0", TensorProto.FLOAT, [1, yolox_anchors(64), 6])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    assert "real outputs differ" in problems(model.SerializeToString())
