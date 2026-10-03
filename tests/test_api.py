import asyncio
import json

import pytest
from sqlalchemy import func, select

from rtagent.storage import previews


async def test_draft_inspection_is_live_and_does_not_persist(studio_client):
    """Inspect unsaved input, variables, and file context without creating execution records."""
    client, app = studio_client
    studio = app.state.studio
    studio.registry.files.write("AGENTS.md", "Keep the project small.")
    bootstrap = (await client.get("/api/v1/bootstrap")).json()
    preset = bootstrap["presets"][0]
    draft = {**preset, "blocks": [dict(block) for block in preset["blocks"]]}
    draft["blocks"][0].update(
        template="Draft for {{target}}", variables={"target": {"type": "string"}}
    )
    body = {
        "preset": draft,
        "config_revision": bootstrap["config"]["revision"],
        "input": "First input",
        "variables": {"target": "one"},
        "skills": ["skill-creator"],
    }
    first = (await client.post("/api/v1/prompts/inspect", json=body)).json()
    body.update(input="Second input", variables={"target": "two"})
    response = await client.post("/api/v1/prompts/inspect", json=body)
    assert response.status_code == 200, response.text
    second = response.json()
    assert first["input_hash"] != second["input_hash"]
    assert second["compiled"]["messages"][0]["content"] == "Draft for two"
    assert second["compiled"]["messages"][-1]["content"] == "Second input"
    assert {"workspace:AGENTS.md", "workspace:skills", "skill:skill-creator"} <= {
        item["source"] for item in second["compiled"]["messages"]
    }
    assert (await client.get("/api/v1/workspace")).json()["instructions"][
        "content"
    ] == "Keep the project small."
    body["variables"] = {}
    assert (await client.post("/api/v1/prompts/inspect", json=body)).status_code == 422
    current = (await client.get("/api/v1/bootstrap")).json()
    assert current["presets"] == bootstrap["presets"] and not current["sessions"]
    async with studio.repository.engine.connect() as connection:
        assert (
            await connection.execute(select(func.count()).select_from(previews))
        ).scalar_one() == 0


async def start(client, text="Hello"):
    bootstrap = (await client.get("/api/v1/bootstrap")).json()
    session = (await client.post("/api/v1/sessions", json={})).json()
    preview = await client.post(
        "/api/v1/prompts/preview",
        json={
            "session_id": session["id"],
            "expected_session_revision": 0,
            "preset_id": bootstrap["presets"][0]["id"],
            "preset_revision": bootstrap["presets"][0]["revision"],
            "config_revision": bootstrap["config"]["revision"],
            "input": text,
        },
    )
    assert preview.status_code == 200, preview.text
    body = {
        "preview_id": preview.json()["id"],
        "input_hash": preview.json()["input_hash"],
        "expected_session_revision": 0,
    }
    response = await client.post(
        "/api/v1/runs", json=body, headers={"Idempotency-Key": session["id"]}
    )
    assert response.status_code == 202, response.text
    return response.json(), body, session["id"], preview.json()


async def wait_state(client, run_id, expected):
    for _ in range(150):
        response = await client.get(f"/api/v1/runs/{run_id}")
        run = response.json()
        if run["status"] in expected:
            return run
        if run["status"] in {"failed", "cancelled"}:
            pytest.fail(str(run))
        await asyncio.sleep(0.03)
    pytest.fail(f"Run did not reach {expected}: {run}")


async def test_preview_run_trace_and_idempotency(studio_client):
    client, app = studio_client
    run, body, session_id, preview = await start(client)
    duplicate = await client.post(
        "/api/v1/runs", json=body, headers={"Idempotency-Key": session_id}
    )
    assert duplicate.json()["id"] == run["id"]
    completed = await wait_state(client, run["id"], {"succeeded"})
    assert completed["usage"]["input_tokens"] is None
    trace = (await client.get(f"/api/v1/runs/{run['id']}/trace?raw=true")).json()["calls"]
    assert trace[0]["data"]["prepared_request"] == preview["prepared_request"]
    assert trace[0]["data"]["wire_request_body"] is None
    session = (await client.get(f"/api/v1/sessions/{session_id}")).json()
    assert session["revision"] == 1 and session["active_run_id"] is None
    assert sum(message["role"] == "user" for message in session["messages"]) == 1
    assert await app.state.studio.repository.read_events(run["id"], 0)
    events = await client.get(f"/api/v1/runs/{run['id']}/events?after=1")
    ids = [int(line[4:]) for line in events.text.splitlines() if line.startswith("id: ")]
    assert ids == sorted(set(ids)) and min(ids) > 1
    assert (await client.get(f"/api/v1/runs/{run['id']}/events?after=99999")).status_code == 410


async def test_native_file_tool_returns_to_model(studio_client):
    client, _app = studio_client
    run, *_ = await start(client, "/read README.md")
    completed = await wait_state(client, run["id"], {"succeeded"})
    messages = completed["messages"]
    assert [message["role"] for message in messages] == ["assistant", "tool", "assistant"]
    assert messages[1]["tool_call_id"] == messages[0]["tool_calls"][0]["id"]
    assert json.loads(messages[1]["content"])["content"] == "Local tool evidence"
    trace = (await client.get(f"/api/v1/runs/{run['id']}/trace")).json()["calls"]
    assert len([call for call in trace if call["kind"] == "model"]) == 2


async def test_direct_run_retries_and_frozen_memory(studio_client):
    """Direct submissions stay idempotent and memory edits cannot alter an active Run."""
    client, app = studio_client
    memory = app.state.studio.registry.files.memory()
    saved = (
        await client.put(
            "/api/v1/workspace/memory",
            json={
                "content": "Original project note.",
                "expected_hash": memory["hash"],
            },
        )
    ).json()
    bootstrap = (await client.get("/api/v1/bootstrap")).json()
    session = (await client.post("/api/v1/sessions", json={})).json()
    body = {
        "session_id": session["id"],
        "expected_session_revision": 0,
        "preset_id": bootstrap["presets"][0]["id"],
        "preset_revision": bootstrap["presets"][0]["revision"],
        "config_revision": bootstrap["config"]["revision"],
        "input": "/echo pause for edit",
    }
    headers = {"Idempotency-Key": session["id"]}
    response = await client.post("/api/v1/runs", json=body, headers=headers)
    assert response.status_code == 202, response.text
    run = await wait_state(client, response.json()["id"], {"waiting_approval"})
    update = {"content": "Changed project note.", "expected_hash": saved["hash"]}
    assert (await client.put("/api/v1/workspace/memory", json=update)).status_code == 200
    assert (await client.put("/api/v1/workspace/memory", json=update)).status_code == 409
    await client.post(
        f'/api/v1/runs/{run["id"]}/approvals/{run["approval"]["id"]}', json={"decision": "approve"}
    )
    await wait_state(client, run["id"], {"succeeded"})
    repeated = await client.post("/api/v1/runs", json=body, headers=headers)
    assert repeated.json()["id"] == run["id"]
    assert (
        await client.post("/api/v1/runs", json={**body, "input": "Different task"}, headers=headers)
    ).status_code == 409
    trace = (await client.get(f'/api/v1/runs/{run["id"]}/trace')).json()["calls"]
    for call in (item for item in trace if item["kind"] == "model"):
        context = next(
            item
            for item in call["data"]["canonical_messages"]
            if item["source"] == "workspace:memory"
        )
        assert context["content"].endswith("Original project note.")


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_approval_stops_before_execution(studio_client, decision):
    client, app = studio_client
    run, *_ = await start(client, "/echo hello")
    waiting = await wait_state(client, run["id"], {"waiting_approval"})
    trace = await app.state.studio.repository.trace(run["id"])
    assert not any(call["kind"] == "tool" and call["status"] == "succeeded" for call in trace)
    response = await client.post(
        f"/api/v1/runs/{run['id']}/approvals/{waiting['approval']['id']}",
        json={"decision": decision},
    )
    assert response.status_code == 200, response.text
    completed = await wait_state(client, run["id"], {"succeeded"})
    result = next(message for message in completed["messages"] if message["role"] == "tool")[
        "content"
    ]
    assert (
        result == "hello"
        if decision == "approve"
        else json.loads(result)["error"]["code"] == "tool_denied"
    )


async def test_cancel_and_redacted_export(studio_client):
    client, _app = studio_client
    run, *_ = await start(client, "/slow")
    await wait_state(client, run["id"], {"running"})
    response = await client.post(f"/api/v1/runs/{run['id']}/cancel")
    assert response.json()["status"] == "cancelled"
    session = (await client.get(f"/api/v1/sessions/{run['session_id']}")).json()
    assert session["active_run_id"] is None and session["revision"] == 0
    secret_run, *_ = await start(client, "sk-example123456789")
    await wait_state(client, secret_run["id"], {"succeeded"})
    exported = await client.get(f"/api/v1/runs/{secret_run['id']}/export")
    assert "sk-example123456789" not in exported.text
    assert exported.json()["redacted"] is True


async def test_access_boundary_and_stale_preview(studio_client):
    client, _app = studio_client
    assert (
        await client.put("/api/v1/config", json={}, headers={"Origin": "https://untrusted.example"})
    ).status_code == 403
    assert (
        await client.post("/api/v1/sessions", json={}, headers={"Sec-Fetch-Site": "cross-site"})
    ).status_code == 403
    assert (await client.get("/api/v1/bootstrap")).status_code == 200
    assert not client.cookies
    assert (
        await client.get("/api/v1/bootstrap", headers={"Host": "attacker.example"})
    ).status_code == 400
    run, _, session_id, preview = await start(client)
    await wait_state(client, run["id"], {"succeeded"})
    response = await client.post(
        "/api/v1/runs",
        json={
            "preview_id": preview["id"],
            "input_hash": preview["input_hash"],
            "expected_session_revision": 0,
        },
        headers={"Idempotency-Key": "another"},
    )
    assert response.status_code == 409
    assert (await client.delete(f"/api/v1/sessions/{session_id}")).status_code == 204
    assert (await client.get(f"/api/v1/runs/{run['id']}")).status_code == 404


async def test_approval_checkpoint_survives_runner_restart(studio_client):
    from rtagent.runtime import Runner

    client, app = studio_client
    run, *_ = await start(client, "/echo durable")
    waiting = await wait_state(client, run["id"], {"waiting_approval"})
    old = app.state.studio.runner
    task = old.tasks.get(run["id"])
    if task:
        await task
    await old.close()
    replacement = Runner(old.repository, old.registry, old.checkpoint_path)
    await replacement.open()
    app.state.studio.runner = replacement
    try:
        response = await client.post(
            f"/api/v1/runs/{run['id']}/approvals/{waiting['approval']['id']}",
            json={"decision": "approve"},
        )
        assert response.status_code == 200
        completed = await wait_state(client, run["id"], {"succeeded"})
        assert any(message["content"] == "durable" for message in completed["messages"])
    finally:
        await replacement.close()
