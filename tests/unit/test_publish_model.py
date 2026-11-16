import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from publish_model import MODELS, check_bar, load_results, render_card  # noqa: E402


@pytest.mark.parametrize("model", ["yolox-tiny", "rfdetr-nano"])
def test_the_committed_results_pass_the_bar_and_the_card_carries_licence_and_every_metric(model):
    r4, r1 = load_results(4), load_results(1)
    card = render_card(model, r4, r1, commit="abc1234")
    assert card.startswith("---\nlicense: cc-by-sa-4.0")
    assert "SpaceNet on Amazon Web Services" in card
    assert "abc1234" in card and "Apache-2.0" in card
    assert "{" not in card.replace("{{", "")  # every placeholder was filled
    for backend, _, _ in MODELS[model]["files"].values():
        assert f"| {backend} | {r4[backend]['map50']:.3f} |" in card
    first = next(iter(MODELS[model]["files"].values()))[0]
    assert f"{r1[first]['mean_detect_seconds']:.2f}" in card


def test_the_bar_is_per_family_and_rfdetr_nano_passes_it():
    r4 = load_results(4)
    speedup, ratio = check_bar("rfdetr-nano-buildings", r4)  # 1.95x and 90.5% of RF-DETR Small
    assert 1.5 <= speedup < 2 and ratio >= 0.9
    assert check_bar("yolox-tiny-buildings", r4)[0] > 3


def test_a_model_that_misses_the_bar_is_refused():
    r4 = load_results(4)
    slow_nano = {**r4, "rfdetr-nano-buildings": {**r4["rfdetr-nano-buildings"],
                                                 "mean_detect_seconds": 0.70}}
    with pytest.raises(SystemExit, match="1.5x"):
        check_bar("rfdetr-nano-buildings", slow_nano)
    weak = {**r4, "yolox-tiny-buildings": {**r4["yolox-tiny-buildings"], "map50": 0.40}}
    with pytest.raises(SystemExit, match="90%"):
        check_bar("yolox-tiny-buildings", weak)
    with pytest.raises(SystemExit, match="not in the committed results"):
        check_bar("yolox-nano-buildings", r4)


def test_int8_is_refused_if_it_loses_more_than_two_points():
    r4 = load_results(4)
    bad = {**r4, "yolox-tiny-buildings-int8": {**r4["yolox-tiny-buildings-int8"], "map50": 0.50}}
    with pytest.raises(SystemExit, match="2 mAP points"):
        check_bar("yolox-tiny-buildings-int8", bad)
