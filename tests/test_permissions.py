"""Command rules and conversation grants: what they allow, what they never override."""

import shutil

import pytest
from test_api import wait_state

from glassbox.domain import CommandRule, Grant, ToolCall
from glassbox.domain.policies import command_permission, grant_for, granted


def test_command_rules_match_whole_words_and_prefer_the_longest_prefix():
    rules = [
        CommandRule(prefix="npm", permission="ask"),
        CommandRule(prefix="npm test", permission="allow"),
        CommandRule(prefix="rm", permission="deny"),
    ]
    assert command_permission("npm test -- -u", rules) == "allow"
    assert command_permission("npm install left-pad", rules) == "ask"
    assert command_permission("npmx", rules) is None
    assert command_permission("rm -rf build; ls", rules) == "deny"


def test_allow_rules_never_cover_chained_or_substituted_commands():
    rules = [CommandRule(prefix="npm test", permission="allow")]
    for command in (
        "npm test; rm -rf data",
        "npm test && curl x",
        "npm test $(id)",
        "npm test > f",
    ):
        assert command_permission(command, rules) is None


def test_shell_grants_cover_one_program_and_other_grants_the_whole_tool():
    grant = grant_for(ToolCall(id="1", name="bash", args={"command": "pytest -q tests"}))
    assert grant.prefix == "pytest"
    assert granted(ToolCall(id="2", name="bash", args={"command": "pytest x.py"}), [grant])
    assert not granted(ToolCall(id="3", name="bash", args={"command": "pytest; id"}), [grant])
    assert not granted(ToolCall(id="4", name="bash", args={"command": "python x.py"}), [grant])
    echo = Grant(tool="echo")
    assert granted(ToolCall(id="5", name="echo", args={"text": "anything"}), [echo])


async def submit(client, session_id, text):
    """Start a Run in an existing conversation at its current revision."""
    bootstrap = (await client.get("/api/v1/bootstrap")).json()
    session = (await client.get(f"/api/v1/sessions/{session_id}")).json()
    preset = bootstrap["presets"][0]
    body = {
        "session_id": session_id,
        "expected_session_revision": session["revision"],
        "preset_id": preset["id"],
        "preset_revision": preset["revision"],
        "config_revision": bootstrap["config"]["revision"],
        "input": text,
    }
    response = await client.post("/api/v1/runs", json=body, headers={"Idempotency-Key": text})
    assert response.status_code == 202, response.text
    return response.json()


async def test_approving_for_the_conversation_stops_later_prompts_until_revoked(glassbox_client):
    client, _app = glassbox_client
    session_id = (await client.post("/api/v1/sessions", json={})).json()["id"]
    run = await submit(client, session_id, "/echo first")
    waiting = await wait_state(client, run["id"], {"waiting_approval"})
    response = await client.post(
        f"/api/v1/runs/{run['id']}/approvals/{waiting['approval']['id']}",
        json={"decision": "approve_conversation"},
    )
    assert response.status_code == 200, response.text
    await wait_state(client, run["id"], {"succeeded"})
    session = (await client.get(f"/api/v1/sessions/{session_id}")).json()
    assert [(grant["tool"], grant["prefix"]) for grant in session["grants"]] == [("echo", None)]

    # The next call of the granted tool runs unasked, and its receipt names the grant.
    second = await submit(client, session_id, "/echo second")
    await wait_state(client, second["id"], {"succeeded"})
    trace = (await client.get(f"/api/v1/runs/{second['id']}/trace?raw=true")).json()["calls"]
    tool = next(call for call in trace if call["kind"] == "tool")
    assert tool["data"]["decision"] == "allow" and tool["data"]["granted_by"]["tool"] == "echo"

    # Revoking is an ordinary revision-checked edit; the tool asks again afterwards.
    session = (await client.get(f"/api/v1/sessions/{session_id}")).json()
    response = await client.patch(
        f"/api/v1/sessions/{session_id}",
        json={"expected_revision": session["revision"], "grants": []},
    )
    assert response.status_code == 200 and response.json()["grants"] == []
    third = await submit(client, session_id, "/echo third")
    await wait_state(client, third["id"], {"waiting_approval"})


@pytest.mark.skipif(not shutil.which("bwrap"), reason="Runs a real sandboxed command.")
async def test_a_command_rule_allows_a_simple_command_but_not_a_chained_one(glassbox_client):
    client, _app = glassbox_client
    config = (await client.get("/api/v1/bootstrap")).json()["config"]
    config["tools"]["bash"] = "ask"
    config["command_rules"] = [{"prefix": "ls", "permission": "allow"}]
    assert (await client.put("/api/v1/config", json=config)).status_code == 200
    session_id = (await client.post("/api/v1/sessions", json={})).json()["id"]
    plain = await submit(client, session_id, '/tool bash {"command": "ls"}')
    await wait_state(client, plain["id"], {"succeeded"})
    other = (await client.post("/api/v1/sessions", json={})).json()["id"]
    chained = await submit(client, other, '/tool bash {"command": "ls; id"}')
    await wait_state(client, chained["id"], {"waiting_approval"})
