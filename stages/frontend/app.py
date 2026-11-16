"""Web UI on indah: sign in with GitHub or Google (or, with no provider configured, an API key),
then Detect (upload a GeoTIFF, review detections)
and Explore (browse datasets), as tabs of one session (ADR-0015).

The UI is a client of the HTTP API only (V6); see `common.py`.
"""

import os

from benchmark import benchmark_view
from common import Client
from detect import MAX_UPLOAD_MB, detect_view
from explore import explore_view
from finetune import finetune_view
from indah import (
    Button,
    Card,
    Column,
    Principal,
    Session,
    Signal,
    Tabs,
    Text,
    TextInput,
    create_app,
)
from models import models_view
from modelupload import modelupload_view

from geopipe import signin
from train import train_view

SECRET = os.environ.get("GEOPIPE_SESSION_SECRET", "")
PUBLIC_URL = os.environ.get("GEOPIPE_PUBLIC_URL", "http://localhost:8080")
SIGN_IN = (signin.SignIn(providers, SECRET, PUBLIC_URL)
           if (providers := signin.providers_from_env()) else None)


def build_session(user: Principal | None = None, ctx=None) -> Session:
    """One viewer's session; with `user` (signed in by GitHub or Google) it is theirs at once."""
    client = Client(user.id, SECRET) if user else Client()
    message = Signal("Enter your API key to start.")
    async def removed() -> None:  # something was deleted: every list that could show it reloads
        for refresh in (detect_refresh, explore_refresh, model_refresh, tune_refresh,
                        bench_refresh, train_refresh):
            await refresh()

    detect_cards, detect_refresh = detect_view(client)
    explore_cards, explore_refresh = explore_view(client, on_deleted=removed)
    model_cards, model_refresh = models_view(client, on_deleted=removed)

    bench_cards, bench_refresh = benchmark_view(client)

    async def model_added() -> None:  # a new model shows in the model view, Detect and Benchmark
        await model_refresh()
        await detect_refresh()
        await bench_refresh()

    tune_cards, tune_refresh = finetune_view(client, on_model=model_added)

    async def dataset_added() -> None:  # a new dataset shows in Explore and in the trainer's list
        await explore_refresh()
        await tune_refresh()
        await bench_refresh()

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
            await bench_refresh()

    if user and ctx:
        ctx.spawn(sign_in())  # lists load as soon as the page opens
    who = (Card(title="Account", children=[Text(f"Signed in as **{user.name or user.id}**. "
                                                "[Sign out](/logout)", markdown=True)])
           if user else Card(title="Sign in", children=[
               TextInput(client.key, label="API key", password=True, on_submit=sign_in),
               Button("Sign in", on_click=sign_in),
               Text(lambda: message.value)]))
    return Session(Column(children=[
        Text("# geo-pipeline\n\nDetect objects in a GeoTIFF, or explore a dataset.", markdown=True),
        who,
        Tabs(children=[Column(children=detect_cards),
                       Column(children=[*explore_cards, *model_cards]),
                       Column(children=[*train_cards, *tune_cards, *upload_cards, *bench_cards])],
             labels=["Detect", "Explore", "Train"])]))




def who_is_signed_in(request) -> Principal | None:
    login = SIGN_IN.login_of(request)
    return Principal(id=login["tenant"], name=login["name"],
                     expires_at=login["exp"]) if login else None


def build_user_session(ctx) -> Session:
    return build_session(ctx.user, ctx)


if SIGN_IN:  # sign-in is on: every session belongs to a signed-in account (ADR-0019)
    app = SIGN_IN.wrap(create_app(session_factory=build_user_session, auth=who_is_signed_in,
                                  secure=True, secret=SECRET, max_upload_mb=MAX_UPLOAD_MB,
                                  title="geo-pipeline"))
else:
    app = create_app(session_factory=build_session, max_upload_mb=MAX_UPLOAD_MB,
                     title="geo-pipeline")
