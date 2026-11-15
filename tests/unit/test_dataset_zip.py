import io
import json
import zipfile

import pytest
from PIL import Image

from geopipe.dataset_zip import (
    ANNOTATIONS,
    DEFAULT_LIMITS,
    Limits,
    ProblemsFound,
    prepare,
    safe_extract,
)


def png(w=64, h=48) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (10, 120, 60)).save(out, "PNG")
    return out.getvalue()


def coco(files, boxes=None, categories=None):
    """files: names; boxes: {name: [[x, y, w, h], ...]}."""
    boxes = boxes or {}
    images, anns = [], []
    for n, name in enumerate(files, start=1):
        images.append({"id": n, "file_name": name, "width": 64, "height": 48})
        for k, b in enumerate(boxes.get(name, [])):
            anns.append(
                {
                    "id": n * 100 + k,
                    "image_id": n,
                    "category_id": 1,
                    "bbox": b,
                    "area": b[2] * b[3],
                    "iscrowd": 0,
                }
            )
    return {
        "images": images,
        "annotations": anns,
        "categories": categories or [{"id": 1, "name": "building"}],
    }


def make_zip(path, entries: dict[str, bytes | str | dict]):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in entries.items():
            zf.writestr(name, json.dumps(body) if isinstance(body, dict) else body)
    return path


def run(tmp_path, entries, limits=DEFAULT_LIMITS):
    work = tmp_path / f"w{len(list(tmp_path.iterdir()))}"  # a fresh directory per call
    work.mkdir()
    z = make_zip(work / "u.zip", entries)
    safe_extract(z, work / "x", limits)
    return prepare(work / "x", work, limits)


def problems_of(tmp_path, entries, limits=DEFAULT_LIMITS):
    with pytest.raises(ProblemsFound) as e:
        run(tmp_path, entries, limits)
    return "\n".join(e.value.problems)


def roboflow(extra=None, ann=None):
    names = ["a.png", "b.png"]
    return {
        "train/a.png": png(),
        "train/b.png": png(),
        f"train/{ANNOTATIONS}": ann or coco(names, {"a.png": [[1, 1, 10, 10]]}),
        "valid/c.png": png(),
        f"valid/{ANNOTATIONS}": coco(["c.png"]),
        **(extra or {}),
    }


def test_roboflow_style_splits_are_used_as_given(tmp_path):
    out = run(tmp_path, roboflow())
    assert sorted(p.name for p in out.iterdir()) == ["train", "valid"]
    assert len(json.loads((out / "train" / ANNOTATIONS).read_text())["images"]) == 2


def test_a_wrapping_folder_and_val_alias_are_accepted(tmp_path):
    entries = {f"mydata/{k.replace('valid', 'val')}": v for k, v in roboflow().items()}
    assert sorted(p.name for p in run(tmp_path, entries).iterdir()) == ["train", "valid"]


def test_one_annotation_file_is_split_80_10_10_by_sorted_name(tmp_path):
    names = [f"img{n:02d}.png" for n in range(10)]
    entries = {f"images/{n}": png() for n in names}
    entries["annotations.json"] = coco(names[::-1], {n: [[1, 1, 5, 5]] for n in names})
    out = run(tmp_path, entries)
    got = {
        s: sorted(i["file_name"] for i in json.loads((out / s / ANNOTATIONS).read_text())["images"])
        for s in ("train", "valid", "test")
    }
    assert got == {"train": names[:8], "valid": names[8:9], "test": names[9:]}
    assert (out / "train" / "img00.png").exists()  # images are copied beside their annotations


@pytest.mark.parametrize("name", ["../evil.png", "/abs.png", "a/../../evil.png"])
def test_paths_that_escape_are_refused(tmp_path, name):
    z = tmp_path / "u.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(name, b"x")
    with pytest.raises(ProblemsFound, match="unsafe path"):
        safe_extract(z, tmp_path / "x")
    assert not (tmp_path / "evil.png").exists()


def test_symbolic_links_are_refused(tmp_path):
    z = tmp_path / "u.zip"
    with zipfile.ZipFile(z, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(ProblemsFound, match="symbolic link"):
        safe_extract(z, tmp_path / "x")


def test_a_size_bomb_is_stopped_by_bytes_written(tmp_path):
    z = make_zip(tmp_path / "u.zip", {"zeros.bin": "\0" * 5_000_000})  # compresses to ~5 KB
    assert z.stat().st_size < 100_000
    with pytest.raises(ProblemsFound, match="unpacks to more than"):
        safe_extract(z, tmp_path / "x", Limits(max_bytes=1_000_000))


def test_too_many_entries_and_not_a_zip_are_refused(tmp_path):
    z = make_zip(tmp_path / "u.zip", {f"f{n}": "x" for n in range(5)})
    with pytest.raises(ProblemsFound, match="entries"):
        safe_extract(z, tmp_path / "x", Limits(max_files=3))
    (tmp_path / "bad.zip").write_bytes(b"not a zip")
    with pytest.raises(ProblemsFound, match="not a valid zip"):
        safe_extract(tmp_path / "bad.zip", tmp_path / "y")


def test_a_listed_image_missing_from_the_zip_is_named(tmp_path):
    entries = roboflow()
    del entries["train/b.png"]
    assert "train/b.png" in problems_of(tmp_path, entries)


def test_invalid_json_and_non_coco_files_are_reported(tmp_path):
    assert "not valid JSON" in problems_of(tmp_path, roboflow({f"train/{ANNOTATIONS}": "{oops"}))
    assert "not a COCO file" in problems_of(
        tmp_path, roboflow({f"train/{ANNOTATIONS}": {"images": []}})
    )


def test_boxes_outside_the_image_or_without_area_are_reported(tmp_path):
    ann = coco(["a.png", "b.png"], {"a.png": [[60, 40, 30, 30], [1, 1, 0, 5]]})
    text = problems_of(tmp_path, roboflow(ann=ann))
    assert "box outside its image (1)" in text and "box with no area (1)" in text


def test_duplicate_file_names_unreadable_images_and_unknown_categories(tmp_path):
    ann = coco(["a.png", "a.png"], {"a.png": [[1, 1, 5, 5]]})
    ann["annotations"].append({"id": 9, "image_id": 1, "category_id": 7, "bbox": [1, 1, 5, 5]})
    text = problems_of(tmp_path, roboflow(ann=ann))
    assert "duplicate image file name" in text and "unknown category" in text
    assert "unreadable image" in problems_of(tmp_path, roboflow({"train/a.png": b"not an image"}))


def test_splits_with_different_categories_are_refused(tmp_path):
    other = coco(["c.png"], categories=[{"id": 1, "name": "car"}])
    assert "different categories" in problems_of(
        tmp_path, roboflow({f"valid/{ANNOTATIONS}": other})
    )


def test_image_limit_and_empty_dataset(tmp_path):
    assert "limit is 1" in problems_of(tmp_path, roboflow(), Limits(max_images=1))
    entries = {f"train/{ANNOTATIONS}": coco([])}
    assert "no images" in problems_of(tmp_path, entries)


def test_no_recognisable_layout_says_what_was_expected(tmp_path):
    assert "found neither" in problems_of(tmp_path, {"readme.txt": "hi"})


def test_the_report_caps_examples_per_problem(tmp_path):
    names = [f"n{k}.png" for k in range(12)]
    text = problems_of(tmp_path, {f"train/{ANNOTATIONS}": coco(names)})
    assert "(12)" in text and text.count("train/n") == 5 and text.endswith(", ...")
