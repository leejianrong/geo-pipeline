"""The Train tab: bring your own dataset (ADR-0015, S3). The zip goes from the browser straight
to the object store through a presigned PUT, so it never passes through this server."""

from common import Client
from indah import Card, DirectUpload, Signal, Text


def train_view(client: Client):
    """The tab's components. `uploaded` holds the store key of the last finished upload."""
    message = Signal("Choose a zip of a COCO dataset. It is sent straight to the object store.")
    uploaded = Signal("")

    async def get_target(info: dict) -> dict:
        resp = await client.request("POST", "/uploads/dataset")
        if resp.status_code != 200:
            raise RuntimeError(f"could not start the upload ({resp.status_code})")
        body = resp.json()
        uploaded.set(body["key"])
        return {"url": body["upload_url"]}

    def on_done(result: dict) -> None:
        if result["ok"]:
            message.set(f"Uploaded {result['filename']} ({result['size'] / 1e6:.1f} MB).")
        else:
            uploaded.set("")
            message.set(f"Upload of {result['filename']} failed: {result['error']}.")

    cards = [
        Card(title="Dataset", children=[
            DirectUpload(get_target, on_done=on_done, label="Dataset zip", accept=".zip"),
            Text(lambda: message.value),
        ]),
    ]
    return cards, uploaded
