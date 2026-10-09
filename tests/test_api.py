import asyncio
import json

import pytest
from sqlalchemy import func, select

from glassbox.infrastructure.storage import previews


async def test_draft_inspection_is_live_and_does_not_persist(glassbox_client):
    """Inspect unsaved input, variables, and file context without creating execution records."""
    client, app = glassbox_client
    glassbox = app.state.glassbox
    glassbox.registry.files.write("AGENTS.md", "Keep the project small.")
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
    async with glassbox.repository.engine.connect() as connection:
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


async def test_preview_run_trace_and_idempotency(glassbox_client):
    client, app = glassbox_client
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
    assert await app.state.glassbox.repository.read_events(run["id"], 0)
    events = await client.get(f"/api/v1/runs/{run['id']}/events?after=1")
    ids = [int(line[4:]) for line in events.text.splitlines() if line.startswith("id: ")]
    assert ids == sorted(set(ids)) and min(ids) > 1
    assert (await client.get(f"/api/v1/runs/{run['id']}/events?after=99999")).status_code == 410


async def test_native_file_tool_returns_to_model(glassbox_client):
    client, _app = glassbox_client
    run, *_ = await start(client, "/read README.md")
    completed = await wait_state(client, run["id"], {"succeeded"})
    messages = completed["messages"]
    assert [message["role"] for message in messages] == ["assistant", "tool", "assistant"]
    assert messages[1]["tool_call_id"] == messages[0]["tool_calls"][0]["id"]
    assert json.loads(messages[1]["content"])["content"] == "Local tool evidence"
    trace = (await client.get(f"/api/v1/runs/{run['id']}/trace")).json()["calls"]
    assert len([call for call in trace if call["kind"] == "model"]) == 2


async def test_direct_run_retries_and_frozen_memory(glassbox_client):
    """Direct submissions stay idempotent and memory edits cannot alter an active Run."""
    client, app = glassbox_client
    memory = app.state.glassbox.registry.files.memory()
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
async def test_approval_stops_before_execution(glassbox_client, decision):
    client, app = glassbox_client
    run, *_ = await start(client, "/echo hello")
    waiting = await wait_state(client, run["id"], {"waiting_approval"})
    trace = await app.state.glassbox.repository.trace(run["id"])
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


async def test_cancel_and_redacted_export(glassbox_client):
    client, _app = glassbox_client
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


async def test_access_boundary_and_stale_preview(glassbox_client):
    client, _app = glassbox_client
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
    # The page served by this process is same-origin on any port; rebinding still fails on Host.
    same_origin = {"Host": "127.0.0.1:8790", "Origin": "http://127.0.0.1:8790"}
    assert (
        await client.post("/api/v1/folders", json={"name": "Port"}, headers=same_origin)
    ).is_success
    rebound = {"Host": "attacker.example:8790", "Origin": "http://attacker.example:8790"}
    assert (
        await client.post("/api/v1/folders", json={"name": "X"}, headers=rebound)
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


async def test_approval_checkpoint_survives_runner_restart(glassbox_client):
    from glassbox.application.runner import Runner

    client, app = glassbox_client
    run, *_ = await start(client, "/echo durable")
    waiting = await wait_state(client, run["id"], {"waiting_approval"})
    old = app.state.glassbox.runner
    task = old.tasks.get(run["id"])
    if task:
        await task
    await old.close()
    replacement = Runner(old.repository, old.registry, old.checkpoint_path)
    await replacement.open()
    app.state.glassbox.runner = replacement
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


async def test_bootstrap_lists_summaries_and_runs_own_their_snapshot(glassbox_client):
    client, app = glassbox_client
    run, _body, session_id, _preview = await start(client, "/read README.md")
    await wait_state(client, run["id"], {"succeeded"})
    [summary] = (await client.get("/api/v1/bootstrap")).json()["sessions"]
    # Navigation data excludes histories; the session resource still returns them.
    assert "messages" not in summary and summary["message_count"] == 4
    session = (await client.get(f"/api/v1/sessions/{session_id}")).json()
    assert len(session["messages"]) == session["message_count"] == 4
    async with app.state.glassbox.repository.engine.connect() as connection:
        assert (
            await connection.execute(select(func.count()).select_from(previews))
        ).scalar_one() == 0
    trace = (await client.get(f"/api/v1/runs/{run['id']}/trace")).json()["calls"]
    assert [call["kind"] for call in trace] == ["model", "tool", "model"]


async def test_runs_in_different_conversations_execute_concurrently(glassbox_client):
    client, _app = glassbox_client
    first, *_ = await start(client, "/slow")
    second, *_ = await start(client, "/slow")
    both = []
    for _ in range(100):
        both = [
            (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"]
            for run in (first, second)
        ]
        if both == ["running", "running"]:
            break
        await asyncio.sleep(0.03)
    assert both == ["running", "running"]
    for run in (first, second):
        await client.post(f"/api/v1/runs/{run['id']}/cancel")
        await wait_state(client, run["id"], {"cancelled"})


async def direct_run(client, text, **choices):
    """Submit a direct Run in a new conversation with optional model and access choices."""
    boot = (await client.get("/api/v1/bootstrap")).json()
    session = (await client.post("/api/v1/sessions", json={})).json()
    preset = boot["presets"][0]
    body = {
        "session_id": session["id"],
        "expected_session_revision": 0,
        "preset_id": preset["id"],
        "preset_revision": preset["revision"],
        "config_revision": boot["config"]["revision"],
        "input": text,
        **choices,
    }
    response = await client.post("/api/v1/runs", json=body, headers={"Idempotency-Key": text})
    assert response.status_code == 202, response.text
    return response.json()


async def test_run_choices_set_model_and_workspace_access(glassbox_client):
    client, _app = glassbox_client
    simulated = {"provider_id": "simulated", "model": "simulated"}
    asked = await direct_run(client, "/read README.md", model=simulated, access="ask")
    # Reading is not a change, so ask mode leaves it allowed.
    done = await wait_state(client, asked["id"], {"succeeded"})
    assert (done["access"], done["provider_id"], done["model"]) == ("ask", "simulated", "simulated")
    gated = await direct_run(
        client,
        '/tool write_text {"path": "new.txt", "content": "x"}',
        model=simulated,
        access="ask",
    )
    waiting = await wait_state(client, gated["id"], {"waiting_approval"})
    assert waiting["approval"]["name"] == "write_text"
    await client.post(f"/api/v1/runs/{gated['id']}/cancel")
    read_only = await direct_run(
        client, '/tool write_text {"path": "other.txt", "content": "x"}', access="read-only"
    )
    finished = await wait_state(client, read_only["id"], {"succeeded"})
    # The write tool is not offered, so the simulated model cannot call it.
    assert not any(message["role"] == "tool" for message in finished["messages"])
    unknown = {"provider_id": "missing", "model": "x"}
    response = await client.post(
        "/api/v1/prompts/inspect",
        json={
            "preset": (await client.get("/api/v1/bootstrap")).json()["presets"][0],
            "config_revision": (await client.get("/api/v1/bootstrap")).json()["config"]["revision"],
            "model": unknown,
        },
    )
    assert response.status_code == 422 and "not configured" in response.text
    # A default model that names a provider takes its connection from that provider, so an
    # edit that changes the request format under the same provider is rejected, not ignored.
    config = (await client.get("/api/v1/bootstrap")).json()["config"]
    profile = config["providers"][0]
    other = "anthropic" if profile["adapter"] == "openai" else "openai"
    config["provider"].update(profile_id=profile["id"], adapter=other, model="x")
    response = await client.put("/api/v1/config", json=config)
    assert response.status_code == 422 and response.json()["error"]["code"] == "provider_mismatch"


async def test_credentials_are_write_only(glassbox_client):
    client, _app = glassbox_client
    secret = "sk-browser-entered-0123456789"
    saved = await client.put("/api/v1/credentials/GLASSBOX_UI_KEY", json={"value": secret})
    assert saved.json() == {"name": "GLASSBOX_UI_KEY", "source": "stored", "hint": "6789"}
    listed = await client.get("/api/v1/credentials", params={"name": "GLASSBOX_UI_KEY"})
    assert secret not in listed.text and listed.json()[0]["source"] == "stored"
    boot = (await client.get("/api/v1/bootstrap")).json()
    assert secret not in json.dumps(boot)
    assert boot["effort_levels"]["anthropic"][-1] == "max"
    assert (
        await client.put("/api/v1/credentials/not-a-name", json={"value": "x"})
    ).status_code == 422
    removed = await client.delete("/api/v1/credentials/GLASSBOX_UI_KEY")
    assert removed.json()["source"] == "missing"


async def test_global_skills_are_shared_by_projects(glassbox_client, tmp_path):
    client, _app = glassbox_client
    skill = {
        "name": "house-style",
        "description": "Shared review rules.",
        "instructions": "Be brief.",
    }
    saved = (await client.put("/api/v1/skills", json=skill)).json()
    assert saved["scope"] == "global"
    other = tmp_path / "other-project"
    other.mkdir()
    project = (
        await client.post("/api/v1/projects", json={"name": "Other", "workspace_root": str(other)})
    ).json()
    workspace = (await client.get(f"/api/v1/workspace?project_id={project['id']}")).json()
    assert any(item["name"] == "house-style" for item in workspace["skills"])
    conflict = {**skill, "description": "Project copy."}
    response = await client.put(
        f"/api/v1/workspace/skills?project_id={project['id']}", json=conflict
    )
    assert response.status_code == 409 and "global skill" in response.text
    # A project folder created outside Glassbox is reported, not silently shadowed.
    (other / ".agents/skills/house-style").mkdir(parents=True)
    (other / ".agents/skills/house-style/SKILL.md").write_text(
        "---\nname: house-style\ndescription: Copy.\n---\n\nBody.\n"
    )
    workspace = (await client.get(f"/api/v1/workspace?project_id={project['id']}")).json()
    assert "global skill" in workspace["skill_errors"]["house-style"]
    listed = (await client.get("/api/v1/skills")).json()
    assert {item["scope"] for item in listed["skills"]} == {"builtin", "global"}
    response = await client.delete(
        "/api/v1/skills/house-style", params={"expected_hash": saved["hash"]}
    )
    assert response.status_code == 204
    assert (
        await client.delete("/api/v1/skills/debugging", params={"expected_hash": "x"})
    ).status_code == 404


async def test_projects_create_a_missing_folder_only_when_asked(glassbox_client, tmp_path):
    client, _app = glassbox_client
    target = tmp_path / "new" / "project"
    body = {"name": "New", "workspace_root": str(target)}
    missing = await client.post("/api/v1/projects", json=body)
    # A mistyped path is reported, not turned into an empty workspace.
    assert missing.json()["error"]["code"] == "workspace_missing" and not target.exists()
    created = await client.post("/api/v1/projects", json={**body, "create": True})
    assert created.status_code == 201 and target.is_dir()
    assert created.json()["workspace_root"] == str(target)
    inside = tmp_path / "data" / "projects" / "x"
    refused = await client.post(
        "/api/v1/projects", json={"name": "Data", "workspace_root": str(inside), "create": True}
    )
    assert refused.status_code == 422 and not inside.exists()
    file = tmp_path / "file.txt"
    file.write_text("x", encoding="utf-8")
    not_folder = await client.post(
        "/api/v1/projects", json={"name": "File", "workspace_root": str(file), "create": True}
    )
    assert "must be a directory" in not_folder.json()["error"]["message"]
