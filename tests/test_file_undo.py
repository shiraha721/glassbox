"""Undo restores exactly what a Run's file tools changed, and never overwrites later edits."""

from test_api import start, wait_state

from glassbox.domain import Message, ToolCall, digest, net_changes


def test_net_changes_fold_a_runs_receipts_per_path():
    """Each path keeps its state before the Run and the state the Run left; no-ops drop out."""
    entries = [
        {"path": "a.txt", "before": None, "after": "a1"},
        {"path": "b.txt", "before": "b0", "after": "b1"},
        {"path": "a.txt", "before": "a1", "after": "a2"},
        {"path": "b.txt", "before": "b1", "after": "b0"},
        {"path": "c.txt", "before": "c0", "after": None},
    ]
    assert net_changes(entries) == {"a.txt": (None, "a2"), "c.txt": ("c0", None)}


async def test_undo_restores_files_still_as_the_run_left_them(
    glassbox_client, monkeypatch, tmp_path
):
    """Created, edited and deleted files go back byte for byte; a file edited since is kept."""
    client, app = glassbox_client
    workspace = tmp_path / "workspace"
    # CRLF bytes prove the restore is byte-exact rather than re-encoded text.
    (workspace / "old.txt").write_bytes(b"keep\r\nme\r\n")
    original_readme = (workspace / "README.md").read_bytes()

    async def editing_model(_config, compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        if any(message.role == "tool" for message in compiled.messages):
            message = Message(role="assistant", content="Files changed.")
        else:
            patch = "*** Begin Patch\n*** Delete File: old.txt\n*** End Patch"
            message = Message(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="write", name="write_text", args={"path": "notes.txt", "content": "v1"}
                    ),
                    ToolCall(
                        id="edit",
                        name="edit_text",
                        args={"path": "README.md", "old_text": "Local", "new_text": "Changed"},
                    ),
                    ToolCall(
                        id="patch",
                        name="apply_patch",
                        args={
                            "patch": patch,
                            "expected_hashes": {"old.txt": digest("keep\r\nme\r\n")},
                        },
                    ),
                ],
            )
        yield {"kind": "result", "message": message, "usage": {}}

    monkeypatch.setattr("glassbox.application.runner.generate", editing_model)
    run, *_ = await start(client, "Change some files")
    completed = await wait_state(client, run["id"], {"succeeded"})
    assert completed["changed_files"] == ["notes.txt", "README.md", "old.txt"]
    listed = (await client.get(f"/api/v1/runs/{run['id']}/files")).json()["files"]
    assert [(item["path"], item["state"]) for item in listed] == [
        ("notes.txt", "as_left"),
        ("README.md", "as_left"),
        ("old.txt", "as_left"),
    ]
    assert (listed[0]["created"], listed[2]["deleted"]) == (True, True)

    # The user edits one file afterwards; undo must leave that edit alone.
    (workspace / "notes.txt").write_text("user edit", encoding="utf-8")
    response = await client.post(f"/api/v1/runs/{run['id']}/files/revert")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["restored"] == ["README.md", "old.txt"] and result["skipped"] == ["notes.txt"]
    assert (workspace / "README.md").read_bytes() == original_readme
    assert (workspace / "old.txt").read_bytes() == b"keep\r\nme\r\n"
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "user edit"
    states = {item["path"]: item["state"] for item in result["files"]}
    assert states == {"notes.txt": "changed", "README.md": "reverted", "old.txt": "reverted"}
    # The undo is recorded on the Run, and repeating it changes nothing.
    events = await app.state.glassbox.repository.read_events(run["id"], 0)
    assert events[-1]["type"] == "files.reverted"
    assert events[-1]["payload"]["restored"] == ["README.md", "old.txt"]
    again = (await client.post(f"/api/v1/runs/{run['id']}/files/revert")).json()
    assert again["restored"] == [] and again["skipped"] == ["notes.txt"]
    # Saved copies live in the data directory, never in the workspace tools can see.
    assert not any("file-history" in str(path) for path in workspace.rglob("*"))


async def test_a_run_without_file_tools_has_nothing_to_undo(glassbox_client):
    """Runs that only talk report no changed files."""
    client, _app = glassbox_client
    run, *_ = await start(client)
    completed = await wait_state(client, run["id"], {"succeeded"})
    assert completed["changed_files"] == []
    result = (await client.post(f"/api/v1/runs/{run['id']}/files/revert")).json()
    assert result == {"run_id": run["id"], "files": [], "restored": [], "skipped": []}
