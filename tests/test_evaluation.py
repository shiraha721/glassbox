"""Prompt evaluation: deterministic grading and a real comparison over the simulated model."""

import asyncio

from rtagent.domain import EvalCheck, EvalItemResult, Message, final_text, grade, summarize_variant


def test_grade_covers_each_check_kind():
    """Every check kind is a pure byte decision over a Run's success and final text."""
    assert grade(EvalCheck(kind="succeeds"), True, "anything")[0] is True
    # A failed Run never passes, whatever the text says.
    assert grade(EvalCheck(kind="succeeds"), False, "ok")[0] is False
    assert grade(EvalCheck(kind="contains", value="OK"), True, "all ok here")[0] is True
    assert grade(EvalCheck(kind="contains", value="OK", ignore_case=False), True, "ok")[0] is False
    assert grade(EvalCheck(kind="not_contains", value="error"), True, "all good")[0] is True
    assert grade(EvalCheck(kind="equals", value=" done "), True, "done")[0] is True
    assert grade(EvalCheck(kind="regex", value=r"flag\{\w+\}"), True, "flag{abc}")[0] is True
    assert grade(EvalCheck(kind="regex", value=r"nope"), True, "other")[0] is False


def test_final_text_reads_the_last_assistant_message():
    """Grading judges the last assistant text, not tool output or earlier turns."""
    messages = [
        Message(role="assistant", content="", tool_calls=[]),
        Message(role="tool", content="raw tool output", tool_call_id="x"),
        Message(role="assistant", content="The answer is 42."),
    ]
    assert final_text(messages) == "The answer is 42."
    assert final_text([Message(role="user", content="hi")]) == ""


def test_summarize_variant_aggregates_pass_rate_and_average_cost():
    """A variant's summary counts only graded cases and ignores unreported usage in averages."""
    items = [
        EvalItemResult(
            case_id="a", variant_id="v", status="succeeded", passed=True, input_tokens=100
        ),
        EvalItemResult(
            case_id="b", variant_id="v", status="succeeded", passed=False, input_tokens=None
        ),
        EvalItemResult(case_id="c", variant_id="other", status="succeeded", passed=True),
    ]
    summary = summarize_variant("v", "Variant", items)
    assert (summary.graded, summary.passed, summary.pass_rate) == (2, 1, 0.5)
    assert summary.avg_input_tokens == 100.0  # the None item is left out, not counted as zero


async def wait_batch(client, batch_id, timeout=20.0):
    """Poll a comparison batch until it reaches a terminal state."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        batch = (await client.get(f"/api/v1/batches/{batch_id}")).json()
        if batch["status"] in {"succeeded", "failed", "cancelled"}:
            return batch
        await asyncio.sleep(0.05)
    raise AssertionError(f"Batch did not finish: {batch}")


async def test_evaluation_compares_prompts_over_real_runs(studio_client):
    """Define an eval, run every case under every variant, and read graded comparison results."""
    client, _app = studio_client
    bootstrap = (await client.get("/api/v1/bootstrap")).json()
    assert bootstrap["evals"] == []
    preset_id = bootstrap["presets"][0]["id"]
    draft = {
        "name": "Reading check",
        "cases": [
            {
                "name": "reads the file",
                "input": "/read README.md",
                "check": {"kind": "contains", "value": "Local tool evidence"},
            },
            {
                "name": "cannot read it",
                "input": "Summarize the project in one line.",
                "check": {"kind": "contains", "value": "Local tool evidence"},
            },
        ],
        "variants": [
            {"name": "Baseline", "preset_id": preset_id},
            {"name": "Alternate", "preset_id": preset_id},
        ],
    }
    created = (await client.post("/api/v1/evals", json=draft)).json()
    assert created["revision"] == 0 and len(created["cases"]) == 2

    started = (await client.post(f"/api/v1/evals/{created['id']}/batches")).json()
    assert started["total"] == 4 and started["status"] == "running"
    batch = await wait_batch(client, started["id"])
    assert batch["status"] == "succeeded" and batch["completed"] == 4

    # Both variants run the same preset, so each passes the readable case and fails the other.
    for variant in batch["variants"]:
        assert (variant["graded"], variant["passed"], variant["pass_rate"]) == (2, 1, 0.5)
    passing = next(item for item in batch["items"] if item["passed"])
    assert "Local tool evidence" in passing["output"] and passing["run_id"]

    # Eval Runs stay out of the conversation list but remain inspectable by their own ids.
    assert (await client.get("/api/v1/bootstrap")).json()["sessions"] == []
    item = batch["items"][0]
    runs = (await client.get(f"/api/v1/sessions/{item['run_session_id']}/runs")).json()
    assert runs[0]["id"] == item["run_id"]

    # Deleting the evaluation removes its hidden Run sessions too.
    assert (await client.delete(f"/api/v1/evals/{created['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/sessions/{item['run_session_id']}")).status_code == 404


async def test_evaluation_rejects_a_duplicate_name_and_bad_regex(studio_client):
    """Names are unique and a check's regex must compile, so mistakes fail at save time."""
    client, _app = studio_client
    preset_id = (await client.get("/api/v1/bootstrap")).json()["presets"][0]["id"]
    base = {
        "name": "Only one",
        "cases": [{"name": "c", "input": "do it", "check": {"kind": "succeeds"}}],
        "variants": [{"name": "v", "preset_id": preset_id}],
    }
    assert (await client.post("/api/v1/evals", json=base)).status_code == 201
    assert (await client.post("/api/v1/evals", json=base)).status_code == 409
    bad = {
        **base,
        "name": "Bad regex",
        "cases": [{"name": "c", "input": "x", "check": {"kind": "regex", "value": "("}}],
    }
    assert (await client.post("/api/v1/evals", json=bad)).status_code == 422


async def test_an_item_that_cannot_start_fails_alone(studio_client):
    """A variant naming an unknown provider fails its own items; the other variant still runs."""
    client, _app = studio_client
    preset_id = (await client.get("/api/v1/bootstrap")).json()["presets"][0]["id"]
    draft = {
        "name": "Mixed",
        "cases": [{"name": "c", "input": "/read README.md", "check": {"kind": "succeeds"}}],
        "variants": [
            {"name": "Works", "preset_id": preset_id},
            {
                "name": "Broken",
                "preset_id": preset_id,
                "model": {"provider_id": "missing", "model": "x"},
            },
        ],
    }
    created = (await client.post("/api/v1/evals", json=draft)).json()
    started = (await client.post(f"/api/v1/evals/{created['id']}/batches")).json()
    batch = await wait_batch(client, started["id"])
    assert batch["status"] == "succeeded"
    results = {item["variant_id"]: item for item in batch["items"]}
    works, broken = (results[variant["id"]] for variant in created["variants"])
    assert works["passed"] is True
    assert broken["passed"] is False and "could not start" in broken["detail"]
    assert "missing is not configured" in broken["detail"]
