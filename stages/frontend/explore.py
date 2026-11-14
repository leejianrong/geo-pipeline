"""The Explore tab: browse a dataset's images with their ground-truth boxes (ADR-0015, S1)."""

import asyncio

from common import Client, Segmented, Select, data_uri, fetch
from indah import BarStrip, Button, Card, Gallery, ImageOverlay, Row, Signal, Stat, Text

from geopipe.viewer import BOX_COUNT_EDGES, BOX_SIZE_EDGES, dataset_stats, edge_labels, gt_overlay

PAGE = 60  # thumbnails per page
SPLITS = ["train", "valid", "test"]


def explore_view(client: Client):
    """The tab's components and a coroutine that loads the dataset list (called after sign-in)."""
    datasets = Signal([])  # manifests from GET /datasets
    dataset_id = Signal("")
    split = Signal("test")
    page = Signal(0)
    message = Signal("Sign in to explore datasets.")
    thumbs = Signal([])  # Gallery entries for the current page
    files = Signal([])  # file names on the current page
    image_file = Signal("")
    preview = Signal("")
    boxes = Signal([])
    stats = Signal({})

    def current() -> dict | None:
        return next((d for d in datasets.value if d["id"] == dataset_id.value), None)

    def splits_of(d: dict | None) -> list[str]:
        return [s for s in SPLITS if d and s in d["splits"]]

    async def get(path: str):
        return await client.request("GET", f"/datasets/{dataset_id.value}{path}")

    async def open_image() -> None:
        if not image_file.value:
            return
        resp = await get(f"/images/{split.value}/{image_file.value}")
        if resp.status_code != 200:
            message.set(f"Could not open {image_file.value}: {resp.json().get('detail')}")
            return
        body = resp.json()
        preview.set(data_uri(await fetch(body["url"]), "image/png"))
        boxes.set(gt_overlay(body["boxes"], body["width"], body["height"], "building"))

    async def load_page() -> None:
        resp = await get(f"/images/{split.value}?offset={page.value * PAGE}&limit={PAGE}")
        if resp.status_code != 200:
            message.set(f"Could not list images: {resp.json().get('detail')}")
            return
        items = resp.json()
        raw = await asyncio.gather(*(fetch(i["thumb_url"]) for i in items))
        thumbs.set(
            [
                {
                    "src": data_uri(b, "image/jpeg"),
                    "alt": i["file"],
                    "caption": f"{i['file']} · {i['boxes']} boxes",
                }
                for b, i in zip(raw, items, strict=True)
            ]
        )
        files.set([i["file"] for i in items])
        image_file.set(items[0]["file"] if items else "")
        await open_image()

    async def load_split() -> None:
        d = current()
        if not d:
            return
        if split.value not in d["splits"]:
            split.set(splits_of(d)[0])
        page.set(0)
        resp = await get(f"/annotations/{split.value}")
        if resp.status_code == 200:
            stats.set(dataset_stats(resp.json()))
        n = d["splits"][split.value]
        message.set(f"{d['name']}: {n} images in {split.value}.")
        await load_page()

    async def load_datasets() -> None:
        resp = await client.request("GET", "/datasets")
        if resp.status_code != 200:
            return
        datasets.set(resp.json())
        if datasets.value and not current():
            dataset_id.set(datasets.value[0]["id"])
        await load_split()

    async def turn(step: int) -> None:
        d = current()
        last = max(0, ((d["splits"][split.value] if d else 0) - 1) // PAGE)
        page.set(min(max(page.value + step, 0), last))
        await load_page()

    async def previous() -> None:
        await turn(-1)

    async def following() -> None:
        await turn(1)

    series = lambda key: lambda: stats.value.get(key, [0])  # noqa: E731

    def caption(key: str, edges: list[float]):
        return lambda: " · ".join(
            f"{name}: {n}"
            for name, n in zip(
                edge_labels(edges), stats.value.get(key, [0] * len(edges)), strict=True
            )
        )

    cards = [
        Card(
            title="Dataset",
            children=[
                Select(
                    dataset_id,
                    options=lambda: [d["id"] for d in datasets.value],
                    label="Dataset",
                    on_change=load_split,
                ),
                Segmented(split, options=SPLITS, label="Split", on_change=load_split),
                Text(lambda: message.value),
                Row(
                    children=[
                        Stat(lambda: stats.value.get("images", 0), label="Images"),
                        Stat(lambda: stats.value.get("boxes", 0), label="Boxes"),
                        Stat(lambda: stats.value.get("empty_images", 0), label="Empty images"),
                    ],
                    gap="1rem",
                ),
                BarStrip(series("boxes_per_image"), height=64, label="Images by number of boxes"),
                Text(caption("boxes_per_image", BOX_COUNT_EDGES)),
                BarStrip(
                    series("box_sizes"),
                    height=64,
                    label="Boxes by size (pixels, square root of area)",
                ),
                Text(caption("box_sizes", BOX_SIZE_EDGES)),
            ],
        ),
        Card(
            title="Licence and attribution",
            children=[
                Text(
                    lambda: (
                        f"**{(d := current()) and d['licence']}**\n\n{d['attribution']}"
                        if current()
                        else ""
                    ),
                    markdown=True,
                )
            ],
        ),
        Card(
            title="Images",
            children=[
                Gallery(thumbs, columns=6),
                Row(
                    children=[
                        Button("Previous page", on_click=previous, variant="ghost"),
                        Button("Next page", on_click=following, variant="ghost"),
                        Text(lambda: f"page {page.value + 1}"),
                    ],
                    gap="0.5rem",
                ),
            ],
        ),
        Card(
            title="Ground truth",
            children=[
                Select(
                    image_file, options=lambda: files.value, label="Image", on_change=open_image
                ),
                ImageOverlay(
                    preview,
                    boxes=lambda: boxes.value,
                    label_mode="hover",
                    alt="a dataset image with its ground-truth boxes",
                ),
            ],
        ),
    ]
    return cards, load_datasets
