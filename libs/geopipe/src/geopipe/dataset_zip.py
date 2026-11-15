"""Untrusted dataset zips (ADR-0017): unzip safely, find the splits, check the COCO files.

Everything here works on local files and raises `ProblemsFound` with a readable list when the
input is bad, so the trainer can show the owner what to fix. Nothing in a zip is trusted: not its
paths, its sizes, its JSON or its images.
"""

import json
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

SPLITS = ("train", "valid", "test")
SPLIT_ALIASES = {
    "val": "valid",
    "validation": "valid",
    "valid": "valid",
    "train": "train",
    "test": "test",
}
ANNOTATIONS = "_annotations.coco.json"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
EXAMPLES = 5  # how many instances of one problem the report shows


@dataclass(frozen=True)
class Limits:
    """ADR-0017: at most 5 GB unzipped and 20,000 images."""

    max_bytes: int = 5 * 1024**3
    max_images: int = 20_000
    max_files: int = 50_000


DEFAULT_LIMITS = Limits()


class ProblemsFound(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def safe_extract(zip_path: Path, dest: Path, limits: Limits = DEFAULT_LIMITS) -> None:
    """Extract `zip_path` into `dest`, refusing anything that could escape or exhaust the disk.

    Refused: a file that is not a zip, absolute or `..` paths, symbolic links, more than
    `max_files` entries, and more than `max_bytes` in total. The total is counted from the bytes
    actually written, not from the sizes the archive declares, because a bomb can lie."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise ProblemsFound(["the upload is not a valid zip file"]) from e
    with zf:
        infos = zf.infolist()
        if len(infos) > limits.max_files:
            raise ProblemsFound(
                [f"the zip has {len(infos)} entries; the limit is {limits.max_files}"]
            )
        root = dest.resolve()
        written = 0
        for info in infos:
            name = info.filename
            target = (dest / name).resolve()
            if (
                name.startswith(("/", "\\"))
                or ".." in Path(name).parts
                or not target.is_relative_to(root)
            ):
                raise ProblemsFound([f"unsafe path in the zip: {name!r}"])
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ProblemsFound([f"the zip contains a symbolic link: {name!r}"])
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                while chunk := src.read(1 << 20):
                    written += len(chunk)
                    if written > limits.max_bytes:
                        raise ProblemsFound(
                            [f"the zip unpacks to more than {limits.max_bytes / 1024**3:.1f} GB"]
                        )
                    out.write(chunk)


def _find_root(extracted: Path) -> Path:
    """Descend through a single wrapping folder (and a macOS `__MACOSX` sibling)."""
    root = extracted
    while True:
        entries = [p for p in root.iterdir() if p.name != "__MACOSX" and not p.name.startswith(".")]
        if (
            len(entries) == 1
            and entries[0].is_dir()
            and entries[0].name.lower() not in SPLIT_ALIASES
        ):
            root = entries[0]
        else:
            return root


def _load_coco(path: Path, where: str, problems: list[str]) -> dict | None:
    try:
        coco = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        problems.append(f"{where}: not valid JSON ({e})")
        return None
    if not isinstance(coco, dict) or not all(
        isinstance(coco.get(k), list) for k in ("images", "annotations", "categories")
    ):
        problems.append(
            f"{where}: not a COCO file (needs lists named images, annotations, categories)"
        )
        return None
    return coco


def _split_dirs(root: Path) -> dict[str, Path]:
    found = {}
    for p in root.iterdir():
        split = SPLIT_ALIASES.get(p.name.lower())
        if p.is_dir() and split and (p / ANNOTATIONS).exists():
            found[split] = p
    return found


def _single_annotation_file(root: Path) -> Path | None:
    candidates = [p for p in root.glob("*.json")]
    named = [p for p in candidates if p.name in (ANNOTATIONS, "annotations.json")]
    return (named or candidates)[0] if len(named or candidates) == 1 else None


def _images_by_name(root: Path, problems: list[str]) -> dict[str, Path]:
    by_name: dict[str, Path] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES:
            if p.name in by_name:
                problems.append(f"two images are called {p.name}; file names must be unique")
            by_name[p.name] = p
    return by_name


def _split_80_10_10(coco: dict, images: dict[str, Path], out: Path) -> dict[str, Path]:
    """S-A1: no split in the zip, so split by sorted file name, 80/10/10."""
    infos = sorted(coco["images"], key=lambda i: Path(str(i.get("file_name", ""))).name)
    n = len(infos)
    cut = {
        "train": (0, round(n * 0.8)),
        "valid": (round(n * 0.8), round(n * 0.9)),
        "test": (round(n * 0.9), n),
    }
    dirs = {}
    for split, (a, b) in cut.items():
        chosen = infos[a:b]
        if not chosen:
            continue
        d = out / split
        d.mkdir(parents=True)
        ids = {i["id"] for i in chosen}
        sub = {
            **coco,
            "images": chosen,
            "annotations": [x for x in coco["annotations"] if x.get("image_id") in ids],
        }
        for info in chosen:
            name = Path(str(info.get("file_name", ""))).name
            if name in images:
                shutil.copy(images[name], d / name)
        (d / ANNOTATIONS).write_text(json.dumps(sub))
        dirs[split] = d
    return dirs


def _check_split(split: str, d: Path, limits: Limits, problems: list[str]) -> dict | None:
    """Validate one split directory in place; rewrite file names to their base names."""
    where = f"{split}/{ANNOTATIONS}"
    coco = _load_coco(d / ANNOTATIONS, where, problems)
    if coco is None:
        return None
    found: dict[str, list[str]] = {}

    def note(kind: str, detail: str) -> None:
        found.setdefault(kind, []).append(detail)

    cat_ids = {c.get("id") for c in coco["categories"] if isinstance(c, dict)}
    seen_ids, seen_names = set(), set()
    sizes: dict[int, tuple[int, int]] = {}
    if len(coco["images"]) > limits.max_images:
        problems.append(f"{where}: {len(coco['images'])} images; the limit is {limits.max_images}")
        return None
    for info in coco["images"]:
        if not isinstance(info, dict) or "id" not in info or "file_name" not in info:
            note("an image entry has no id or file_name", where)
            continue
        name = Path(str(info["file_name"])).name
        info["file_name"] = name
        if info["id"] in seen_ids:
            note("duplicate image id", str(info["id"]))
        if name in seen_names:
            note("duplicate image file name", name)
        seen_ids.add(info["id"])
        seen_names.add(name)
        path = d / name
        if not path.is_file():
            note("image listed in the annotations but missing from the zip", f"{split}/{name}")
            continue
        try:
            with Image.open(path) as im:
                size = im.size
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            note("unreadable image", f"{split}/{name}")
            continue
        if (info.get("width"), info.get("height")) != size:
            info["width"], info["height"] = size  # trust the pixels, not the metadata
        sizes[info["id"]] = size
    for a in coco["annotations"]:
        box = a.get("bbox") if isinstance(a, dict) else None
        if not (
            isinstance(box, list) and len(box) == 4 and all(isinstance(v, int | float) for v in box)
        ):
            note("annotation with no valid bbox", str(a.get("id") if isinstance(a, dict) else a))
            continue
        x, y, w, h = box
        if a.get("image_id") not in seen_ids:
            note("annotation for an image that is not listed", str(a.get("id")))
        elif a.get("category_id") not in cat_ids:
            note("annotation with an unknown category", str(a.get("id")))
        elif w <= 0 or h <= 0:
            note("box with no area", f"annotation {a.get('id')}")
        elif a["image_id"] in sizes:
            iw, ih = sizes[a["image_id"]]
            if x < -1 or y < -1 or x + w > iw + 1 or y + h > ih + 1:
                note("box outside its image", f"annotation {a.get('id')} on image {a['image_id']}")
    for kind, items in found.items():
        shown = ", ".join(items[:EXAMPLES]) + (", ..." if len(items) > EXAMPLES else "")
        problems.append(f"{where}: {kind} ({len(items)}): {shown}")
    (d / ANNOTATIONS).write_text(json.dumps(coco))
    return coco


def prepare(extracted: Path, workdir: Path, limits: Limits = DEFAULT_LIMITS) -> Path:
    """Turn an unzipped upload into the standard layout (`train|valid|test/` with images and
    `_annotations.coco.json`) under a new directory and return it. Raises `ProblemsFound`."""
    root = _find_root(extracted)
    problems: list[str] = []
    dirs = _split_dirs(root)
    out = workdir / "dataset"
    if dirs:
        out.mkdir()
        for split, d in dirs.items():
            shutil.copytree(d, out / split)
        dirs = {s: out / s for s in dirs}
    else:
        annotations = _single_annotation_file(root)
        if annotations is None:
            raise ProblemsFound(
                [
                    "found neither train/valid/test folders with _annotations.coco.json "
                    "nor a single COCO json file at the top of the zip"
                ]
            )
        coco = _load_coco(annotations, annotations.name, problems)
        images = _images_by_name(root, problems)
        if coco is None or problems:
            raise ProblemsFound(problems)
        out.mkdir()
        dirs = _split_80_10_10(coco, images, out)
    checked = {}
    for split, d in dirs.items():
        if (coco := _check_split(split, d, limits, problems)) is not None:
            checked[split] = coco
    names = {tuple(sorted(c.get("name") for c in coco["categories"])) for coco in checked.values()}
    if len(names) > 1:
        problems.append("the splits list different categories")
    if sum(len(c["images"]) for c in checked.values()) > limits.max_images:
        problems.append(f"more than {limits.max_images} images in total")
    if problems:
        raise ProblemsFound(problems)
    if not any(c["images"] for c in checked.values()):
        raise ProblemsFound(["the dataset has no images"])
    return out
