"""Deterministic grading of one eval case from a Run's outcome and final text.

Grading is pure and offline: a Run's success plus a byte-level check on its last assistant
text. No tokenizer and no model judge, so the same inputs always give the same verdict."""

import re

from rtagent.domain.models import EvalCheck, EvalItemResult, Message, VariantSummary


def final_text(messages: list[Message]) -> str:
    """Return the last assistant message's text, which is what a reader judges a turn by."""
    for message in reversed(messages):
        if message.role == "assistant" and isinstance(message.content, str):
            return message.content
    return ""


def grade(check: EvalCheck, succeeded: bool, output: str) -> tuple[bool | None, str]:
    """Decide whether a case passed, returning the verdict and a short human-readable reason."""
    if not succeeded:
        return False, "The Run did not succeed, so the check was not applied."
    if check.kind == "succeeds":
        return True, "The Run completed successfully."
    text = output or ""
    value = check.value
    if check.ignore_case and check.kind != "regex":
        text, value = text.lower(), value.lower()
    if check.kind == "contains":
        found = value in text
        return found, f"Output {'contains' if found else 'does not contain'} the expected text."
    if check.kind == "not_contains":
        absent = value not in text
        return absent, f"Output {'omits' if absent else 'contains'} the forbidden text."
    if check.kind == "equals":
        equal = text.strip() == value.strip()
        return equal, f"Output {'equals' if equal else 'differs from'} the expected text."
    # regex
    try:
        flags = re.IGNORECASE if check.ignore_case else 0
        matched = re.search(check.value, text, flags) is not None
    except re.error as error:
        return False, f"Invalid regular expression: {error}"
    return matched, f"Pattern {'matched' if matched else 'did not match'} the output."


def summarize_variant(variant_id: str, name: str, items: list[EvalItemResult]) -> VariantSummary:
    """Aggregate one variant's graded cases into a pass rate and average cost per case."""
    mine = [item for item in items if item.variant_id == variant_id]
    graded = [item for item in mine if item.passed is not None]
    passed = sum(1 for item in graded if item.passed)

    def average(values: list[int | float | None], digits: int = 1) -> float | None:
        present = [value for value in values if value is not None]
        return round(sum(present) / len(present), digits) if present else None

    return VariantSummary(
        variant_id=variant_id,
        name=name,
        graded=len(graded),
        passed=passed,
        pass_rate=round(passed / len(graded), 3) if graded else None,
        avg_input_tokens=average([item.input_tokens for item in graded]),
        avg_output_tokens=average([item.output_tokens for item in graded]),
        avg_active_seconds=average([item.active_seconds for item in graded]),
        avg_cost=average([item.cost for item in graded], 6),
    )
