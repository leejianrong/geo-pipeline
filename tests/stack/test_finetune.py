"""S4: the Train tab's fine-tune section through the indah session, against the live stack. The
trainer runs with TRAINER_DEVICE=fake here (`make e2e`), so a run takes a couple of seconds and
trains nothing, but everything around it is real: the API, the queue, the slot, the registry."""

import asyncio

import pytest
from conftest import BUCKET
from test_explore import write_split
from test_frontend import labelled, new_session, nodes, send, sign_in

from geopipe import bus, registry, store


def value(component, prop="value"):
    return component.reactive_props()[prop]()


@pytest.fixture(scope="module", autouse=True)
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("finetune")
    write_split(root, "train", {"a.png": [[1, 1, 9, 9]], "b.png": []})
    write_split(root, "valid", {"c.png": [[2, 2, 8, 8]]})
    r, s3 = bus.connect(), store.client()
    store.ensure_bucket(s3, BUCKET)
    registry.register_dataset(
        s3,
        r,
        BUCKET,
        root,
        "ft-data",
        scope="acme",
        name="Ft",
        licence="CC BY 4.0",
        attribution="made up",
    )
    yield
    r.delete("tenant:acme:datasets", "tenant:acme:trainruns", "tenant:acme:models")


def runs_table(session):
    """The "Your runs" table: Detect's and the benchmark's tables have other columns."""
    return next(
        t
        for t in nodes(session, "table")
        if any(c["title"] == "preset" for c in t.static_props()["columns"])
    )


def texts(session):
    return [str(value(t, "text")) for t in nodes(session, "text") if "text" in t.reactive_props()]


async def set_epochs(session, n):
    await send(session, labelled(session, "number", "Epochs"), "input", {"value": n})


@pytest.mark.asyncio
async def test_a_run_shows_progress_ends_in_a_model_and_it_reaches_the_model_view():
    session = new_session("ft-a")
    await sign_in(session, "dev-key-a")
    pick = labelled(session, "select", "Training dataset")
    assert "ft-data" in [o["value"] if isinstance(o, dict) else o for o in value(pick, "options")]
    await send(session, pick, "change", {"value": "ft-data"})
    await send(session, labelled(session, "segmented", "Preset"), "change", {"value": "balanced"})
    assert value(labelled(session, "number", "Epochs")) == 60  # the preset's default
    await set_epochs(session, 3)

    await send(session, labelled(session, "button", "Start training"), "click")  # follows the run
    done = next(t for t in texts(session) if t.startswith("Done: model model-"))
    model_id = done.split()[2]
    steps = value(nodes(session, "steps")[-1], "items")
    assert [s[1] for s in steps] == ["done", "done", "done"]  # [label, state, detail]
    assert steps[2][2] == model_id
    loss = [c for c in nodes(session, "chart") if value(c, "data")]
    assert [len(value(c, "data")) for c in loss] == [3, 3]  # loss and AP, one point per epoch
    assert value(nodes(session, "progress")[-1], "value") == 1.0
    assert any("epoch 3/3" in t and "validation AP50 0.500" in t for t in texts(session))
    table = runs_table(session)
    assert [(r["state"], r["model"]) for r in value(table, "rows")] == [("done", model_id)]
    options = value(labelled(session, "select", "Inspect model"), "options")
    assert model_id in [o["value"] if isinstance(o, dict) else o for o in options]


@pytest.mark.asyncio
async def test_cancel_stops_a_run_in_progress():
    session = new_session("ft-b")
    await sign_in(session, "dev-key-a")
    await set_epochs(session, 100)  # about thirty seconds if left alone
    start = asyncio.create_task(
        send(session, labelled(session, "button", "Start training"), "click")
    )
    cancel = labelled(session, "button", "Cancel run")
    for _ in range(100):  # wait until it is running
        await asyncio.sleep(0.2)
        if any("running" in t and "epoch" in t for t in texts(session)):
            break
    await send(session, cancel, "click")
    await asyncio.wait_for(start, 30)
    assert "The run was cancelled." in texts(session)
    rows = value(runs_table(session), "rows")
    cancelled = [r for r in rows if r["state"] == "cancelled"]  # not rows[0]: the clock can step
    assert len(cancelled) == 1 and cancelled[0]["model"] == ""


@pytest.mark.asyncio
async def test_another_tenant_is_not_offered_the_dataset_and_never_sees_the_runs():
    session = new_session("ft-c")
    await sign_in(session, "dev-key-b")
    options = value(labelled(session, "select", "Training dataset"), "options")
    assert "ft-data" not in [o["value"] if isinstance(o, dict) else o for o in options]
    assert value(runs_table(session), "rows") == []


@pytest.mark.asyncio
async def test_a_refresh_does_not_undo_the_epochs_the_viewer_chose():
    session = new_session("ft-d")
    await sign_in(session, "dev-key-a")
    await set_epochs(session, 7)
    await send(session, labelled(session, "button", "Create dataset"), "click")  # any refresh path
    await sign_in(session, "dev-key-a")  # signing in again refreshes every tab
    assert value(labelled(session, "number", "Epochs")) == 7
