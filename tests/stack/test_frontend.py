"""The web UI session against the real API: sign in, upload, progress, detections, filters.

Drives the indah session through the same dispatch path a browser event takes (no browser).
"""

import os
import sys

import pytest
from conftest import make_synth_tif
from indah.components import UploadedFile
from indah.transport import Hub

os.environ["API_URL"] = "http://api:8000"
os.environ["S3_REWRITE"] = "localhost:8333=seaweedfs:8333"
sys.path.insert(0, "/repo/stages/frontend")
import app as ui  # noqa: E402


def nodes(session, type_):
    return [c for c in session._by_id.values() if c.type == type_]


def labelled(session, type_, label):
    return next(c for c in nodes(session, type_)
                if c.static_props().get("label") == label or
                c.reactive_props().get("label", lambda: None)() == label)


def text_of(component) -> str:
    return str(component.reactive_props()["blocks"]()) if "blocks" in component.reactive_props() \
        else str(component.reactive_props()["text"]())


async def send(session, node, event, payload=None):
    result = session.dispatch(node.id, event, payload or {})
    if result is not None and getattr(result, "coro", None) is not None:
        await result.coro


def new_session(tab):
    session = ui.build_session()
    session.session_id = tab
    session.bind_hub(Hub())
    return session


async def sign_in(session, key):
    await send(session, labelled(session, "textinput", "API key"), "input", {"value": key})
    await send(session, labelled(session, "button", "Sign in"), "click")


@pytest.mark.asyncio
async def test_sign_in_upload_review_and_filter(tmp_path):
    tif = tmp_path / "synth.tif"
    make_synth_tif(str(tif))
    session = new_session("tab-a")

    await sign_in(session, "wrong")
    assert "not accepted" in str([text_of(t) for t in nodes(session, "text")])

    await sign_in(session, "dev-key-a")
    await send(session, labelled(session, "select", "Model"), "change", {"value": "stub"})
    await send(session, nodes(session, "upload")[0], "upload",
               {"files": [UploadedFile("synth.tif", "image/tiff", tif.read_bytes())]})

    overlay = nodes(session, "imageoverlay")[0].reactive_props()
    assert overlay["src"]().startswith("data:image/jpeg;base64,")
    assert len(overlay["boxes"]()) == 5  # the five rectangles in the synthetic raster
    assert all(0 <= b["x"] <= 1 and 0 < b["w"] <= 1 for b in overlay["boxes"]())

    class_filter = labelled(session, "textinput", "Class contains")
    await send(session, class_filter, "input", {"value": "no such class"})
    assert overlay["boxes"]() == []
    await send(session, class_filter, "input", {"value": "bright"})
    assert len(overlay["boxes"]()) == 5

    row = nodes(session, "table")[0].reactive_props()["rows"]()[0]  # newest first
    assert row["state"] == "done" and row["boxes"] == 5
    assert nodes(session, "download")[0].reactive_props()["href"]()  # a link to the GeoJSON

    # another tenant's key sees none of it
    other = new_session("tab-b")
    await sign_in(other, "dev-key-b")
    assert nodes(other, "table")[0].reactive_props()["rows"]() == []
