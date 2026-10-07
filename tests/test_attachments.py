"""Attached images: stored by hash, referenced in records, filled in only as a request is sent."""

import base64
import json

import httpx
import pytest
from test_api import wait_state
from test_providers import mock_client, sse

from rtagent.domain import AppError, Attachment, Message, Preset, ProviderConfig, StudioConfig
from rtagent.domain.prompts import compile_prompt
from rtagent.infrastructure.attachments import AttachmentStore
from rtagent.infrastructure.providers.attachments import attachment_data
from rtagent.infrastructure.providers.chat import generate, prepare

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
GIF = b"GIF89a" + b"\x01" * 10


def test_the_store_keeps_known_image_types_once(tmp_path):
    store = AttachmentStore(tmp_path / "attachments")
    first = store.save(PNG)
    assert first.media_type == "image/png" and first.size == len(PNG)
    assert store.save(PNG) == first and len(list((tmp_path / "attachments").iterdir())) == 1
    assert store.info(first.hash) == first and store.load(first.hash) == PNG
    with pytest.raises(AppError) as svg:
        store.save(b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>")
    assert svg.value.code == "attachment_type"
    with pytest.raises(AppError) as large:
        store.save(PNG + b"\x00" * 5 * 1024 * 1024)
    assert large.value.status == 413
    with pytest.raises(AppError) as unknown:
        store.info("../credentials.json")
    assert unknown.value.code == "attachment_missing"


def compiled_with_image(provider: ProviderConfig, image: Attachment):
    return compile_prompt(
        Preset(name="Empty"),
        StudioConfig(provider=provider),
        [],
        "What is in this picture?",
        {},
        [],
        attachments=[image],
    )


@pytest.mark.parametrize("adapter", ["openai", "anthropic"])
async def test_requests_carry_references_until_they_are_sent(monkeypatch, tmp_path, adapter):
    """Previews and recorded bodies name the image; only the outgoing bytes contain it."""
    image = AttachmentStore(tmp_path).save(PNG)
    encoded = base64.b64encode(PNG).decode()
    provider = ProviderConfig(adapter=adapter, model="vision", base_url="http://127.0.0.1:1/v1")
    compiled = compiled_with_image(provider, image)
    prepared = prepare(provider, compiled, [])
    sent = []

    if adapter == "openai":
        parts = prepared["messages"][-1]["content"]
        assert parts[1] == {
            "type": "image_url",
            "image_url": {"url": f"attachment-data-url:{image.hash}"},
        }
        chunk = {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "vision",
            "choices": [{"index": 0, "delta": {"content": "A cat."}, "finish_reason": "stop"}],
        }
        reply = httpx.Response(
            200,
            text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )
        module = "chat"
    else:
        blocks = prepared["messages"][-1]["content"]
        assert blocks[0] == {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": f"attachment-base64:{image.hash}",
            },
        }
        assert blocks[1]["type"] == "text" and "cache_control" in blocks[1]
        reply = httpx.Response(
            200,
            text=sse(
                {"type": "message_start", "message": {"id": "m", "model": "x", "usage": {}}},
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "A cat."},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 2},
                },
                {"type": "message_stop"},
            ),
            headers={"content-type": "text/event-stream"},
        )
        module = "messages"

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content.decode())
        return reply

    mock_client(monkeypatch, module, handler)
    recorded = []

    async def on_wire(body):
        recorded.append(body)

    token = attachment_data.set({image.hash: ("image/png", encoded)})
    try:
        events = [event async for event in generate(provider, compiled, [], prepared, on_wire, "k")]
    finally:
        attachment_data.reset(token)
    assert events[-1]["message"].content == "A cat."
    assert recorded == [prepared]
    assert encoded in sent[0] and "attachment-" not in sent[0]
    assert encoded not in json.dumps(recorded)


async def test_an_attached_image_reaches_the_run_and_its_history(studio_client):
    """Upload, run with the image, and keep it on the user message in history."""
    client, _app = studio_client
    upload = await client.post(
        "/api/v1/attachments", content=GIF, headers={"Content-Type": "image/gif"}
    )
    assert upload.status_code == 201, upload.text
    image = upload.json()
    served = await client.get(f"/api/v1/attachments/{image['hash']}")
    assert served.content == GIF and served.headers["content-type"] == "image/gif"
    assert served.headers["x-content-type-options"] == "nosniff"
    rejected = await client.post("/api/v1/attachments", content=b"<html>")
    assert rejected.status_code == 415

    bootstrap = (await client.get("/api/v1/bootstrap")).json()
    session = (await client.post("/api/v1/sessions", json={})).json()
    request = {
        "session_id": session["id"],
        "expected_session_revision": 0,
        "preset_id": bootstrap["presets"][0]["id"],
        "preset_revision": bootstrap["presets"][0]["revision"],
        "config_revision": bootstrap["config"]["revision"],
        "input": "Describe the image.",
        "attachments": [image["hash"]],
    }
    response = await client.post(
        "/api/v1/runs", json=request, headers={"Idempotency-Key": session["id"]}
    )
    assert response.status_code == 202, response.text
    completed = await wait_state(client, response.json()["id"], {"succeeded"})
    assert completed["attachments"] == [image]
    assert "Attached images:** 1" in completed["messages"][-1]["content"]
    history = (await client.get(f"/api/v1/sessions/{session['id']}")).json()["messages"]
    assert history[0]["attachments"] == [image]
    assert Message.model_validate(history[0]).attachments[0].hash == image["hash"]

    missing = {**request, "expected_session_revision": 1, "attachments": ["0" * 64]}
    failed = await client.post("/api/v1/runs", json=missing, headers={"Idempotency-Key": "x"})
    assert failed.status_code == 404


async def test_the_runner_supplies_image_data_only_to_its_model_call(studio_client, monkeypatch):
    """The data for the request's own images is available while the request is sent."""
    client, _app = studio_client
    image = (await client.post("/api/v1/attachments", content=PNG)).json()
    seen = []

    async def model(_config, compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        seen.append((attachment_data.get(), compiled.messages[-1].attachments))
        yield {"kind": "result", "message": Message(role="assistant", content="ok"), "usage": {}}

    monkeypatch.setattr("rtagent.application.runner.generate", model)
    bootstrap = (await client.get("/api/v1/bootstrap")).json()
    session = (await client.post("/api/v1/sessions", json={})).json()
    response = await client.post(
        "/api/v1/runs",
        json={
            "session_id": session["id"],
            "expected_session_revision": 0,
            "preset_id": bootstrap["presets"][0]["id"],
            "preset_revision": bootstrap["presets"][0]["revision"],
            "config_revision": bootstrap["config"]["revision"],
            "input": "Look.",
            "attachments": [image["hash"]],
        },
        headers={"Idempotency-Key": session["id"]},
    )
    await wait_state(client, response.json()["id"], {"succeeded"})
    data, attached = seen[0]
    assert data == {image["hash"]: ("image/png", base64.b64encode(PNG).decode())}
    assert [item.hash for item in attached] == [image["hash"]]
    assert attachment_data.get() is None
