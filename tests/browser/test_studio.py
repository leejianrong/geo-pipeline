"""Browser smoke test of the studio: a real Chromium against the running stack (`make browser`).

It does what a user does: sign in, upload a small dataset zip in the Train tab, wait for it to be
checked, open it in Explore and look for its ground-truth boxes. Everything between the browser
and the store is the real thing. It needs the stack up (`make up`) and does not touch the stack
tests' container; set STUDIO_URL / STUDIO_KEY for another address or key, and STUDIO_CHROMIUM for a
Chromium that Playwright did not download (the snap one cannot read /tmp, so STUDIO_TMP names a
folder under home for the zip).
"""

import io
import json
import os
import re
import tempfile
import uuid
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import expect, sync_playwright

ORIGIN = os.environ.get("STUDIO_URL", "http://localhost:8080")
KEY = os.environ.get("STUDIO_KEY", "dev-key-a")
BOXES = [[5, 5, 30, 20], [50, 10, 20, 20], [80, 50, 25, 30]]
GROUND_TRUTH = "a dataset image with its ground-truth boxes"


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (120, 90), (90, 120, 60)).save(out, "PNG")
    return out.getvalue()


def make_zip(path: Path, boxes, extra_listed=()) -> Path:
    names = ["a.png", *extra_listed]
    coco = {
        "images": [
            {"id": n, "file_name": f, "width": 120, "height": 90}
            for n, f in enumerate(names, start=1)
        ],
        "annotations": [
            {"id": k, "image_id": 1, "category_id": 1, "bbox": b, "iscrowd": 0}
            for k, b in enumerate(boxes, start=1)
        ],
        "categories": [{"id": 1, "name": "roof"}],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("train/a.png", png())
        zf.writestr("train/_annotations.coco.json", json.dumps(coco))
    return path


@pytest.fixture(scope="module")
def workdir():
    with tempfile.TemporaryDirectory(dir=os.environ.get("STUDIO_TMP")) as d:
        yield Path(d)


@pytest.fixture
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("STUDIO_CHROMIUM"), args=["--no-sandbox"]
        )
        page = browser.new_page(viewport={"width": 1300, "height": 1600})
        page.set_default_timeout(30_000)
        page.goto(ORIGIN)
        yield page
        browser.close()


def sign_in(page, key=KEY):
    page.get_by_label("API key").fill(key)
    page.get_by_role("button", name="Sign in").click()


def upload(page, zip_path: Path, name: str) -> None:
    page.get_by_role("tab", name="Train").click()
    page.get_by_label("Dataset zip").set_input_files(str(zip_path))
    expect(page.get_by_text("Name it and press Create dataset").first).to_be_visible()
    page.get_by_label("Dataset name").fill(name)
    page.get_by_label("Dataset licence").fill("CC0 1.0")
    page.get_by_label("Dataset attribution").fill("made up for a test")
    page.get_by_role("button", name="Create dataset").click()


def test_a_wrong_key_is_refused_and_the_right_one_signs_in(page):
    sign_in(page, "not-a-key")
    expect(page.get_by_text("That API key was not accepted.")).to_be_visible()
    sign_in(page)
    expect(page.get_by_text("Signed in.")).to_be_visible()


def test_an_uploaded_dataset_shows_its_ground_truth_boxes_in_explore(page, workdir):
    name = f"Smoke {uuid.uuid4().hex[:6]}"
    sign_in(page)
    upload(page, make_zip(workdir / "good.zip", BOXES), name)
    expect(page.get_by_text(f"Ready: 1 images, {len(BOXES)} boxes").first).to_be_visible()

    page.get_by_role("tab", name="Explore").click()
    select = page.get_by_label("Dataset", exact=True)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower())
    entry = select.locator(f"option[value^='{slug}']")
    expect(entry).to_have_count(1)  # the list reloads right after the upload is checked
    select.select_option(entry.get_attribute("value"))

    overlay = page.locator(".overlay-wrap", has=page.get_by_alt_text(GROUND_TRUTH))
    expect(overlay.locator("img")).to_have_attribute("src", re.compile("^data:image/png"))
    expect(overlay.locator(".ov-box")).to_have_count(len(BOXES))  # fails if boxes are missing


def test_a_dataset_with_a_missing_image_is_rejected_with_a_report(page, workdir):
    sign_in(page)
    upload(
        page,
        make_zip(workdir / "bad.zip", BOXES, extra_listed=["gone.png"]),
        f"Broken {uuid.uuid4().hex[:6]}",
    )
    expect(page.get_by_text("The dataset was rejected:").first).to_be_visible()
    expect(page.get_by_text("train/gone.png", exact=False).first).to_be_visible()
