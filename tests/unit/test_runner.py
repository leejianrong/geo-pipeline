import os
import sys
import time

import pytest

from geopipe import bus
from geopipe.runner import run_command, train_and_validation_split
from geopipe.train import Cancelled


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_every_output_line_reaches_the_callback_and_a_clean_exit_returns_them():
    got = []
    lines = run_command(py("print('a'); print('b')"), on_line=got.append)
    assert got == lines == ["a", "b"]


def test_a_failed_trainer_reports_the_tail_of_its_output_permanently():
    code = "print('loading'); print('CUDA out of memory'); raise SystemExit(3)"
    with pytest.raises(bus.PermanentError, match=r"exit 3.*loading \| CUDA out of memory"):
        run_command(py(code))


def test_environment_and_working_directory_are_passed(tmp_path):
    out = run_command(
        py("import os; print(os.environ['X'], os.getcwd())"), env={"X": "1"}, cwd=tmp_path
    )
    assert out == [f"1 {tmp_path}"]


def test_tick_runs_while_the_trainer_is_quiet():
    ticks = []
    run_command(py("import time; time.sleep(0.6)"), tick=lambda: ticks.append(1), poll=0.1)
    assert len(ticks) >= 3


def test_cancel_stops_the_process_and_its_children_quickly(tmp_path):
    pidfile = tmp_path / "child.pid"
    child = (
        "import subprocess, sys, time; "
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"open({str(pidfile)!r}, 'w').write(str(p.pid)); print('started', flush=True); "
        "time.sleep(60)"
    )
    started = time.monotonic()
    with pytest.raises(Cancelled):
        run_command(py(child), cancelled=lambda: pidfile.exists(), poll=0.1)
    assert time.monotonic() - started < 15
    grandchild = int(pidfile.read_text())
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild, 0)  # the child of the trainer is gone too


def test_validation_split_is_valid_else_test_and_train_is_required(tmp_path):
    for name in ("train", "test"):
        (tmp_path / name).mkdir()
    assert train_and_validation_split(tmp_path) == (tmp_path / "train", tmp_path / "test")
    (tmp_path / "valid").mkdir()
    assert train_and_validation_split(tmp_path)[1] == tmp_path / "valid"
    (tmp_path / "train").rmdir()
    with pytest.raises(bus.PermanentError, match="train split"):
        train_and_validation_split(tmp_path)
