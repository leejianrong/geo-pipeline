"""Studio routes (ADR-0015): datasets, for now. Read-only; images come as presigned URLs.

A tenant sees its own datasets and the shared ones, and nothing else. Ids and file names are
checked against strict patterns because they become object keys.
"""

from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException

from . import registry, store


def thumb_key(split: str, file_name: str) -> str:
    return f"thumbs/{split}/{file_name.rsplit('.', 1)[0]}.jpg"


def add_dataset_routes(app: FastAPI, r, s3, public_s3, bucket: str, tenant: Callable) -> None:
    def manifest(dataset_id: str, tid: str) -> tuple[str, registry.DatasetManifest]:
        hit = registry.find(s3, bucket, tid, dataset_id)
        if not hit or (hit[1].state != "ready" and hit[0] != tid):
            raise HTTPException(404, "unknown dataset")  # same answer for another tenant's
        return hit

    def annotations(scope: str, dataset_id: str, split: str) -> dict:
        coco = registry.read_annotations(s3, bucket, scope, dataset_id, split) \
            if split in registry.SPLITS else None
        if coco is None:
            raise HTTPException(404, "unknown split")
        return coco

    def url(scope: str, dataset_id: str, rest: str) -> str:
        return store.presign_get(public_s3, bucket, f"{registry.prefix(scope, dataset_id)}/{rest}")

    @app.get("/datasets")
    def list_datasets(tid: str = Depends(tenant)) -> list[dict]:
        return [{**m.model_dump(), "scope": "shared" if scope == registry.SHARED else "own"}
                for scope, m in registry.list_datasets(r, s3, bucket, tid)]

    @app.get("/datasets/{dataset_id}")
    def get_dataset(dataset_id: str, tid: str = Depends(tenant)) -> dict:
        scope, m = manifest(dataset_id, tid)
        return {**m.model_dump(), "scope": "shared" if scope == registry.SHARED else "own"}

    @app.get("/datasets/{dataset_id}/annotations/{split}")
    def get_annotations(dataset_id: str, split: str, tid: str = Depends(tenant)) -> dict:
        scope, _ = manifest(dataset_id, tid)
        return annotations(scope, dataset_id, split)

    @app.get("/datasets/{dataset_id}/images/{split}")
    def list_images(dataset_id: str, split: str, offset: int = 0, limit: int = 60,
                    tid: str = Depends(tenant)) -> list[dict]:
        """One page of images with thumbnail URLs and the number of ground-truth boxes."""
        scope, _ = manifest(dataset_id, tid)
        coco = annotations(scope, dataset_id, split)
        counts: dict[int, int] = {}
        for a in coco["annotations"]:
            counts[a["image_id"]] = counts.get(a["image_id"], 0) + 1
        page = coco["images"][max(0, offset):max(0, offset) + max(1, min(limit, 200))]
        return [{"file": i["file_name"], "width": i["width"], "height": i["height"],
                 "boxes": counts.get(i["id"], 0),
                 "thumb_url": url(scope, dataset_id, thumb_key(split, i["file_name"]))}
                for i in page]

    @app.get("/datasets/{dataset_id}/images/{split}/{file}")
    def get_image(dataset_id: str, split: str, file: str, tid: str = Depends(tenant)) -> dict:
        """One image: its URL, size and ground-truth boxes as pixel [x, y, w, h]."""
        scope, _ = manifest(dataset_id, tid)
        if not registry.FILE_RE.match(file):
            raise HTTPException(404, "unknown image")
        coco = annotations(scope, dataset_id, split)
        info = next((i for i in coco["images"] if i["file_name"] == file), None)
        if info is None:
            raise HTTPException(404, "unknown image")
        return {"url": url(scope, dataset_id, f"{split}/{file}"), "width": info["width"],
                "height": info["height"],
                "boxes": [a["bbox"] for a in coco["annotations"] if a["image_id"] == info["id"]]}
