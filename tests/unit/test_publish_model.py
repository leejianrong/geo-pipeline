import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from publish_model import FILES, check_bar, load_results, render_card  # noqa: E402


def test_the_committed_results_pass_the_bar_and_the_card_carries_licence_and_every_metric():
    r4, r1 = load_results(4), load_results(1)
    card = render_card(r4, r1, commit="abc1234")
    assert card.startswith("---\nlicense: cc-by-sa-4.0")
    assert "SpaceNet on Amazon Web Services" in card
    assert "abc1234" in card and "Apache-2.0" in card
    for backend in (b for b, _, _ in FILES.values()):
        assert f"| {backend} | {r4[backend]['map50']:.3f} |" in card
    assert f"{r1['yolox-tiny-buildings']['mean_detect_seconds']:.2f}" in card


def test_a_model_that_misses_the_bar_is_refused():
    r4 = load_results(4)
    with pytest.raises(SystemExit, match="does not pass the bar"):
        check_bar("rfdetr-nano-buildings", r4)  # accurate, but slower than YOLOX-s
    slow = {**r4, "yolox-tiny-buildings": {**r4["yolox-tiny-buildings"], "map50": 0.40}}
    with pytest.raises(SystemExit, match="90%"):
        check_bar("yolox-tiny-buildings", slow)
    with pytest.raises(SystemExit, match="not in the committed results"):
        check_bar("yolox-nano-buildings", r4)


def test_int8_is_refused_if_it_loses_more_than_two_points():
    r4 = load_results(4)
    bad = {**r4, "yolox-tiny-buildings-int8": {**r4["yolox-tiny-buildings-int8"], "map50": 0.50}}
    with pytest.raises(SystemExit, match="2 mAP points"):
        check_bar("yolox-tiny-buildings-int8", bad)
