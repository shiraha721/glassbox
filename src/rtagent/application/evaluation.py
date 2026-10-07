"""Comparison batches over the normal Run path: run each case under each prompt, then grade.

An evaluation names cases (tasks with a pass condition) and variants (prompts to compare). A
batch runs every case under every variant as an ordinary Run in a hidden eval session, grades
the final text deterministically, and aggregates each variant's pass rate and average cost. This
reuses submission, the Runner and Run evidence rather than adding a second execution engine."""

from typing import Any

from rtagent.domain import (
    Eval,
    EvalBatchView,
    EvalItemResult,
    EvalLabel,
    EvalSummary,
    Message,
    final_text,
    grade,
    summarize_variant,
)
from rtagent.domain.lifecycle import TERMINAL


def eval_record(row: dict[str, Any]) -> Eval:
    """Rebuild the typed evaluation definition from its stored row."""
    data = row["data"]
    return Eval(
        id=row["id"],
        name=row["name"],
        revision=row["revision"],
        created_at=row["created_at"],
        cases=data["cases"],
        variants=data["variants"],
    )


def eval_summary(row: dict[str, Any]) -> EvalSummary:
    """Summarize an evaluation and its most recent batch for the list view."""
    data = row["data"]
    last = row.get("last_batch")
    return EvalSummary(
        id=row["id"],
        name=row["name"],
        revision=row["revision"],
        created_at=row["created_at"],
        case_count=len(data["cases"]),
        variant_count=len(data["variants"]),
        last_batch_id=last["id"] if last else None,
        last_status=last["status"] if last else None,
        last_run_at=last["created_at"] if last else None,
    )


def batch_view(row: dict[str, Any]) -> EvalBatchView:
    """Project a batch into progress, per-variant aggregates and every case/variant result."""
    data = row["data"]
    items = [EvalItemResult.model_validate(item) for item in data["items"]]
    return EvalBatchView(
        id=row["id"],
        eval_id=row["eval_id"],
        eval_name=data["eval_name"],
        status=row["status"],
        created_at=row["created_at"],
        completed=sum(1 for item in items if item.status in TERMINAL),
        total=len(items),
        error=data.get("error"),
        cases=[EvalLabel(**label) for label in data["cases"]],
        variants=[
            summarize_variant(variant["id"], variant["name"], items) for variant in data["variants"]
        ],
        items=items,
    )


def graded_item(
    case_id: str, variant_id: str, run_id: str | None, run: dict[str, Any], check: Any
) -> EvalItemResult:
    """Grade one completed Run against its case check and record its usage snapshot."""
    messages = [Message.model_validate(item) for item in run.get("messages", [])]
    output = final_text(messages)
    passed, detail = grade(check, run["status"] == "succeeded", output)
    usage = run.get("usage") or {}
    return EvalItemResult(
        case_id=case_id,
        variant_id=variant_id,
        run_id=run_id,
        status=run["status"],
        passed=passed,
        detail=detail,
        output=output[:2000],
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        active_seconds=usage.get("active_seconds"),
        cost=usage.get("cost"),
    )
