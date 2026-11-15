"""The Train tab: bring your own dataset (ADR-0015, S3). The zip goes from the browser straight
to the object store through a presigned PUT, so it never passes through this server; the form
then names it and the trainer stage validates it."""

from common import Client, wait_job
from indah import Button, Card, DirectUpload, Signal, Text, TextInput

POLL_SECONDS = 1.0
GIVE_UP_SECONDS = 1800  # the trainer stalls the job's status long before this


def bullets(report: str) -> str:
    """A report with one problem per line, as a Markdown list."""
    return "\n".join(f"- {line}" for line in report.splitlines())


def train_view(client: Client, on_registered=None):
    """The tab's components and a coroutine that loads the viewer's datasets (after sign-in).
    `on_registered` runs when a dataset becomes ready (the Explore tab reloads its list)."""
    message = Signal("Choose a zip of a COCO dataset. It is sent straight to the object store.")
    upload_key = Signal("")
    name, licence, attribution = Signal(""), Signal(""), Signal("")
    outcome = Signal("")  # the validation report or the counts
    mine = Signal([])  # the viewer's own datasets

    async def get_target(info: dict) -> dict:
        resp = await client.request("POST", "/uploads/dataset", json={"size": info["size"]})
        if resp.status_code != 200:
            reason = resp.json().get("detail", resp.status_code)
            message.set(f"Cannot upload {info['filename']}: {reason}")
            raise RuntimeError(reason)
        body = resp.json()
        upload_key.set(body["key"])
        return {"url": body["upload_url"]}

    def on_done(result: dict) -> None:
        if result["ok"]:
            message.set(f"Uploaded {result['filename']} ({result['size'] / 1e6:.1f} MB). "
                        "Name it and press Create dataset.")
        else:
            upload_key.set("")
            message.set(f"Upload of {result['filename']} failed: {result['error']}.")

    async def refresh() -> None:
        resp = await client.request("GET", "/datasets")
        if resp.status_code == 200:
            mine.set([d for d in resp.json() if d["scope"] == "own"])

    async def create() -> None:
        if not upload_key.value:
            outcome.set("Upload a zip first.")
            return
        if not (name.value.strip() and licence.value.strip()):
            outcome.set("A dataset needs a name and a licence.")
            return
        resp = await client.request("POST", "/datasets", json={
            "upload_key": upload_key.value, "name": name.value.strip(),
            "licence": licence.value.strip(), "attribution": attribution.value})
        if resp.status_code != 202:
            outcome.set(f"Could not create the dataset: {resp.json().get('detail')}")
            return
        outcome.set("Checking the dataset...")
        status = await wait_job(client, resp.json()["job_id"], POLL_SECONDS, GIVE_UP_SECONDS)
        if status is None:
            outcome.set("Still checking; come back to this tab in a few minutes.")
            return
        upload_key.set("")
        await refresh()
        if status["state"] == "done" and on_registered:
            await on_registered()  # the other tabs list it before this tab says it is there
        if status["state"] == "done":
            outcome.set(f"Ready: {status['images']} images, {status['boxes']} boxes. "
                        "It is in the Explore tab.")
        else:
            outcome.set(f"The dataset was rejected:\n\n{bullets(status['error'])}")

    def listing() -> str:
        return "\n".join(
            f"- **{d['name']}**: {d['state']}"
            + (f", {sum(d['splits'].values())} images, {d['boxes']} boxes" if d["splits"] else "")
            + (f"\n\n{bullets(d['report'])}" if d["report"] else "")
            for d in mine.value) or "No datasets of your own yet."

    cards = [
        Card(title="Dataset", children=[
            DirectUpload(get_target, on_done=on_done, label="Dataset zip", accept=".zip"),
            Text(lambda: message.value),
            TextInput(name, label="Dataset name"),
            TextInput(licence, label="Dataset licence"),
            TextInput(attribution, label="Dataset attribution"),
            Button("Create dataset", on_click=create),
            Text(lambda: outcome.value, markdown=True),
        ]),
        Card(title="Your datasets", children=[Text(listing, markdown=True)]),
    ]
    return cards, refresh
