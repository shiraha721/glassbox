"""Custom slash commands expand saved templates on the server and are recorded on the Run."""

from test_api import start, wait_state

from rtagent.domain import Message
from rtagent.domain.commands import expand_template, parse_invocation
from rtagent.infrastructure.workspace.files import Workspace


def test_invocations_are_recognized_only_at_the_start():
    """Only `/name` at the very start invokes a command; arguments keep their text."""
    assert parse_invocation("/review src/app.py  focus\non errors") == (
        "review",
        "src/app.py  focus\non errors",
    )
    assert parse_invocation("/fix-tests") == ("fix-tests", "")
    assert parse_invocation("please /review this") is None
    assert parse_invocation("/Review x") is None
    assert parse_invocation("/usr/bin/env python") is None


def test_templates_fill_placeholders_or_append_arguments():
    """$ARGUMENTS takes everything, $1..$9 take words; unused arguments are never dropped."""
    assert expand_template("Review $1 for $2.\nAll: $ARGUMENTS", "a.py bugs") == (
        "Review a.py for bugs.\nAll: a.py bugs"
    )
    assert expand_template("Compare $1 and $3.", "only") == "Compare only and ."
    assert expand_template("Summarize the changes.\n", "in two lines") == (
        "Summarize the changes.\n\nin two lines"
    )
    assert expand_template("Summarize the changes.", "") == "Summarize the changes."


def test_project_commands_replace_global_ones_and_report_bad_files(tmp_path):
    """Both folders are read; a project command of the same name wins."""
    root, data = tmp_path / "work", tmp_path / "data"
    (root / ".agents" / "commands").mkdir(parents=True)
    (data / "commands").mkdir(parents=True)
    (data / "commands" / "review.md").write_text("Global review of $ARGUMENTS", encoding="utf-8")
    (data / "commands" / "plan.md").write_text("Plan $ARGUMENTS", encoding="utf-8")
    (root / ".agents" / "commands" / "review.md").write_text(
        "---\ndescription: Review a file\nargument-hint: <path>\n---\nProject review of $1\n",
        encoding="utf-8",
    )
    (root / ".agents" / "commands" / "Bad Name.md").write_text("x", encoding="utf-8")
    (root / ".agents" / "commands" / "empty.md").write_text("---\n---\n", encoding="utf-8")
    workspace = Workspace(root, data)
    found, errors = workspace.commands()
    assert [(item["name"], item["scope"]) for item in found] == [
        ("plan", "global"),
        ("review", "project"),
    ]
    assert found[1]["description"] == "Review a file" and found[1]["argument_hint"] == "<path>"
    assert set(errors) == {"Bad Name.md", "empty"}
    text, use = workspace.expand_command("/review a.py")
    assert text == "Project review of a.py" and use["scope"] == "project"
    # Input naming no command is left alone, so a literal leading slash still works.
    assert workspace.expand_command("/missing thing") == ("/missing thing", None)


async def test_a_command_run_records_what_was_typed_and_sends_the_expansion(
    studio_client, monkeypatch, tmp_path
):
    """The model receives the expanded template; the Run keeps the command and arguments."""
    client, _app = studio_client
    commands = tmp_path / "workspace" / ".agents" / "commands"
    commands.mkdir(parents=True)
    (commands / "review.md").write_text(
        "---\ndescription: Review one file\n---\nReview $1 carefully. Focus: $ARGUMENTS\n",
        encoding="utf-8",
    )
    listed = (await client.get("/api/v1/workspace/commands")).json()
    assert listed == [
        {
            "name": "review",
            "description": "Review one file",
            "argument_hint": "",
            "scope": "project",
        }
    ]
    seen = []

    async def recording_model(_config, compiled, _tools, _prepared, on_wire, _key=""):
        await on_wire(None)
        seen.append(compiled.messages[-1].content)
        yield {"kind": "result", "message": Message(role="assistant", content="Done."), "usage": {}}

    monkeypatch.setattr("rtagent.application.runner.generate", recording_model)
    run, _body, session_id, preview = await start(client, "/review app.py errors")
    expanded = "Review app.py carefully. Focus: app.py errors"
    assert preview["command"]["name"] == "review"
    completed = await wait_state(client, run["id"], {"succeeded"})
    assert seen == [expanded]
    assert completed["input"] == expanded
    assert completed["command"]["arguments"] == "app.py errors"
    assert completed["command"]["scope"] == "project" and len(completed["command"]["hash"]) == 64
    session = (await client.get(f"/api/v1/sessions/{session_id}")).json()
    assert session["title"] == "/review app.py errors"
    # Editing the template later leaves the recorded Run unchanged.
    (commands / "review.md").write_text("Changed $ARGUMENTS", encoding="utf-8")
    again = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert again["input"] == expanded
