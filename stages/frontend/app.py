"""Web UI on indah: sign in with an API key, then Detect (upload a GeoTIFF, review detections)
and Explore (browse datasets), as tabs of one session (ADR-0015).

The UI is a client of the HTTP API only (V6); see `common.py`.
"""


from common import Client
from detect import MAX_UPLOAD_MB, detect_view
from explore import explore_view
from finetune import finetune_view
from indah import Button, Card, Column, Session, Signal, Tabs, Text, TextInput, create_app
from models import models_view
from modelupload import modelupload_view

from train import train_view


def build_session() -> Session:
    client = Client()
    message = Signal("Enter your API key to start.")
    detect_cards, detect_refresh = detect_view(client)
    explore_cards, explore_refresh = explore_view(client)
    model_cards, model_refresh = models_view(client)

    async def model_added() -> None:  # a trained model shows in the model view and in Detect
        await model_refresh()
        await detect_refresh()

    tune_cards, tune_refresh = finetune_view(client, on_model=model_added)

    async def dataset_added() -> None:  # a new dataset shows in Explore and in the trainer's list
        await explore_refresh()
        await tune_refresh()

    train_cards, train_refresh = train_view(client, on_registered=dataset_added)
    upload_cards = modelupload_view(client, on_registered=model_added)

    async def sign_in() -> None:
        resp = await client.request("GET", "/jobs")
        client.signed_in.set(resp.status_code == 200)
        message.set("Signed in." if resp.status_code == 200 else "That API key was not accepted.")
        if resp.status_code == 200:
            await detect_refresh()
            await explore_refresh()
            await model_refresh()
            await train_refresh()
            await tune_refresh()

    return Session(Column(children=[
        Text("# geo-pipeline\n\nDetect objects in a GeoTIFF, or explore a dataset.", markdown=True),
        Card(title="Sign in", children=[
            TextInput(client.key, label="API key", password=True, on_submit=sign_in),
            Button("Sign in", on_click=sign_in),
            Text(lambda: message.value)]),
        Tabs(children=[Column(children=detect_cards),
                       Column(children=[*explore_cards, *model_cards]),
                       Column(children=[*train_cards, *tune_cards, *upload_cards])],
             labels=["Detect", "Explore", "Train"])]))


app = create_app(session_factory=build_session, max_upload_mb=MAX_UPLOAD_MB, title="geo-pipeline")
