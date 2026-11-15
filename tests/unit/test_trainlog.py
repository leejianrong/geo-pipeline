from pathlib import Path

from geopipe.trainlog import YoloxLog, rfdetr_progress

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_yolox_progress_and_validation_ap_from_a_real_log():
    log = YoloxLog()
    updates = [
        u
        for line in (FIXTURES / "yolox_train_log_excerpt.txt").read_text().splitlines()
        if (u := log.feed(line))
    ]
    progress = [u for u in updates if "loss" in u]
    assert [u["epoch"] for u in progress][:3] == [1, 2, 3]
    assert all(u["epochs"] == 60 and u["loss"] > 0 for u in progress)
    aps = [u for u in updates if "val_ap" in u]
    assert [u["val_ap"] for u in aps] == [0.293, 0.304]  # the AP at IoU 0.5 after epochs 5 and 10
    assert [u["epoch"] for u in aps] == [5, 10]
    assert progress[0]["loss"] == 10.5 and progress[0]["epoch"] == 1


def test_yolox_lines_that_carry_nothing_are_ignored():
    log = YoloxLog()
    assert log.feed("2026-11-14 09:27:00.987 | INFO | Load fast_cocoeval op in 8.755s.") is None
    assert (
        log.feed(" Average Precision  (AP) @[ IoU=0.50 | area=   all | maxDets=100 ] = 0.5") is None
    )  # before any epoch line there is nothing to attach it to


def test_a_yolox_ap_of_minus_one_means_no_data_and_is_reported_as_zero():
    log = YoloxLog()
    log.feed("epoch: 5/60, iter: 10/15, mem: 1Mb, iter_time: 1s, data_time: 0s, total_loss: 5.0")
    line = " Average Precision  (AP) @[ IoU=0.50      | area=   all | maxDets=100 ] = -1.000"
    assert log.feed(line)["val_ap"] == 0.0


def test_rfdetr_epochs_from_a_real_metrics_file():
    updates = rfdetr_progress((FIXTURES / "rfdetr_metrics.csv").read_text())
    assert [u["epoch"] for u in updates] == list(range(1, 31))
    first = updates[0]
    assert abs(first["val_ap"] - 0.1819796860218048) < 1e-12 and first["loss"] > 6
    assert all("val_ap" in u and "loss" in u for u in updates[1:])
    assert updates[-1]["val_ap"] > first["val_ap"]  # it learned


def test_rfdetr_metrics_with_no_rows_or_a_partial_file():
    assert rfdetr_progress("") == []
    assert rfdetr_progress("epoch,step,train/loss\n") == []
    assert rfdetr_progress("epoch,step,train/loss\n0,5,2.5\nbroken") == [{"epoch": 1, "loss": 2.5}]
