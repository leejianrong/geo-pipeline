"""The Train tab's "bring your own model": an ONNX file goes from the browser straight to the
object store, then the trainer stage checks it and registers it (ADR-0017, S6)."""

from common import Client, Segmented, wait_job
from indah import Button, Card, DirectUpload, Signal, Text, TextInput

from train import bullets

DECODERS = ["yolox", "rfdetr"]


def modelupload_view(client: Client, on_registered=None):
    """The section's components. `on_registered` runs when a model is registered."""
    message = Signal("Choose an ONNX file (a YOLOX or an RF-DETR export, at most 500 MB).")
    upload_key = Signal("")
    name, licence, classes = Signal(""), Signal(""), Signal("")
    decoder = Signal("yolox")
    outcome = Signal("")

    async def get_target(info: dict) -> dict:
        resp = await client.request("POST", "/uploads/model", json={"size": info["size"]})
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
                        "Describe it and press Register model.")
        else:
            upload_key.set("")
            message.set(f"Upload of {result['filename']} failed: {result['error']}.")

    async def create() -> None:
        names = [c.strip() for c in classes.value.split(",") if c.strip()]
        if not upload_key.value:
            outcome.set("Upload an ONNX file first.")
        elif not (name.value.strip() and licence.value.strip() and names):
            outcome.set("A model needs a name, a licence and at least one class name.")
        else:
            resp = await client.request("POST", "/models", json={
                "upload_key": upload_key.value, "name": name.value.strip(),
                "decoder": decoder.value, "classes": names, "licence": licence.value.strip()})
            if resp.status_code != 202:
                outcome.set(f"Could not register the model: {resp.json().get('detail')}")
                return
            outcome.set("Checking the model...")
            status = await wait_job(client, resp.json()["job_id"])
            upload_key.set("")
            if status is None:
                outcome.set("Still checking; come back in a few minutes.")
            elif status["state"] == "done":
                if on_registered:
                    await on_registered()  # the other tabs list it before this one says so
                outcome.set(f"Registered: a {status['input_size']} px {decoder.value} model. "
                            "It is in the Detect tab and the model view.")
            else:
                outcome.set(f"The model was rejected:\n\n{bullets(status['error'])}")

    cards = [
        Card(title="Bring your own model", children=[
            DirectUpload(get_target, on_done=on_done, label="Model ONNX", accept=".onnx"),
            Text(lambda: message.value),
            TextInput(name, label="Model name"),
            TextInput(licence, label="Model licence"),
            Segmented(decoder, options=DECODERS, label="Output format"),
            TextInput(classes, label="Class names, comma separated", placeholder="building"),
            Button("Register model", on_click=create),
            Text(lambda: outcome.value, markdown=True),
        ]),
    ]
    return cards
