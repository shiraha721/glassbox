"""Deterministic prompt composition; no I/O or model SDK imports."""

import json
import math
import re
from typing import Any

from rtagent.domain import AppError, CompiledPrompt, Message, Preset, StudioConfig, digest

VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z_0-9]*)\s*\}\}")


def condition_value(condition: dict[str, Any], context: dict[str, Any], depth: int = 0) -> bool:
    """Evaluate the restricted condition grammar; reject malformed or missing fields."""
    if depth > 12:
        raise AppError("invalid_condition", "Conditions may nest at most 12 levels.")
    keys = set(condition)
    if len(keys) == 1 and keys <= {"all", "any", "not"}:
        operator = next(iter(keys))
        value = condition[operator]
        if operator == "not":
            if not isinstance(value, dict):
                raise AppError("invalid_condition", "not requires a condition object.")
            return not condition_value(value, context, depth + 1)
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise AppError("invalid_condition", "all/any require a list of conditions.")
        results = [condition_value(item, context, depth + 1) for item in value]
        return all(results) if operator == "all" else any(results)
    if not {"field", "op"} <= keys or keys - {"field", "op", "value"}:
        raise AppError("invalid_condition", "Use all/any/not or field/op/value.")
    field, operator = condition["field"], condition["op"]
    if not isinstance(field, str) or operator not in {"eq", "in", "exists"}:
        raise AppError("invalid_condition", "Unsupported field or operator.")
    if field.startswith("variables.") and field.count(".") == 1:
        values, field = context["variables"], field.split(".")[1]
    elif field in {"provider", "model", "task_type", "tools"}:
        values = context
    else:
        raise AppError("invalid_condition", f"Unsupported condition field: {field}")
    if operator == "exists":
        return field in values and values[field] is not None
    if field not in values or values[field] is None:
        raise AppError("missing_condition_field", f"Condition field is missing: {field}")
    if "value" not in condition:
        raise AppError("invalid_condition", "Comparison requires a value.")
    if operator == "in":
        if not isinstance(condition["value"], list):
            raise AppError("invalid_condition", "in requires a list value.")
        return values[field] in condition["value"]
    return values[field] == condition["value"]


def compile_prompt(
    preset: Preset,
    config: StudioConfig,
    history: list[Message],
    user_input: str,
    variables: dict[str, Any],
    tools: list[dict[str, Any]],
    delta: list[Message] | None = None,
) -> CompiledPrompt:
    """Compile a frozen configuration without I/O or implicit message transformations.

    History, the current input, and runtime results each enter exactly once.
    Invalid ordering, variables, conditions, or context budgets raise AppError."""
    positions, block_ids = set(), set()
    slots: dict[str, list[Message]] = {"before_history": [], "before_input": []}
    report = []
    context = {
        "provider": config.provider.adapter,
        "model": config.provider.model,
        "task_type": variables.get("task_type"),
        "variables": variables,
        "tools": [tool["name"] for tool in tools],
    }
    for block in sorted(preset.blocks, key=lambda item: (item.placement, item.order)):
        position = (block.placement, block.order)
        if position in positions or block.id in block_ids:
            raise AppError(
                "duplicate_order", "Block IDs and orders within a placement must be unique."
            )
        positions.add(position)
        block_ids.add(block.id)
        matches = condition_value(block.condition, context) if block.condition is not None else True
        reason = "included"
        if not block.enabled:
            reason = "disabled"
        elif (
            block.provider_allowlist is not None
            and config.provider.adapter not in block.provider_allowlist
        ):
            reason = "provider_filter"
        elif (
            block.model_allowlist is not None and config.provider.model not in block.model_allowlist
        ):
            reason = "model_filter"
        elif not matches:
            reason = "condition_false"
        entry = {"block_id": block.id, "name": block.name, "reason": reason, "tokens": 0}
        report.append(entry)
        if reason != "included":
            continue
        if "{{" in VARIABLE.sub("", block.template) or "}}" in VARIABLE.sub("", block.template):
            raise AppError("invalid_template", f"Unsupported template syntax in {block.name}.")

        def replace(match: re.Match[str], current=block) -> str:
            name = match[1]
            spec = current.variables.get(name)
            if spec is None:
                raise AppError("undeclared_variable", f"Declare {name} in {current.name}.")
            value = variables.get(name, spec.default)
            if value is None:
                if spec.required:
                    raise AppError(
                        "missing_variable", f"Variable {name} is required by {current.name}."
                    )
                return ""
            valid = (
                (spec.type == "string" and isinstance(value, str))
                or (spec.type == "boolean" and isinstance(value, bool))
                or (spec.type == "number" and type(value) in (int, float))
            )
            if not valid:
                raise AppError("variable_type", f"{name} must be {spec.type}.")
            return value if isinstance(value, str) else json.dumps(value)

        content = VARIABLE.sub(replace, block.template)
        entry["tokens"] = math.ceil(len(content.encode()) / 4)
        slots[block.placement].append(Message(role=block.role, content=content, source=block.id))
    messages = (
        slots["before_history"]
        + history
        + slots["before_input"]
        + [Message(role="user", content=user_input, source="current_input")]
        + (delta or [])
    )
    encoded = [message.model_dump() for message in messages]
    estimate = math.ceil(len(json.dumps({"messages": encoded, "tools": tools}).encode()) / 4)
    limit = config.provider.context_window
    if limit is not None and estimate + config.provider.max_output_tokens > limit:
        raise AppError(
            "context_budget_exceeded",
            "Estimated context exceeds the configured window.",
            details={
                "estimated_input": estimate,
                "reserved_output": config.provider.max_output_tokens,
                "window": limit,
            },
        )
    return CompiledPrompt(
        messages=messages,
        activation_report=report,
        source_map=[
            {"message_index": index, "source": message.source}
            for index, message in enumerate(messages)
        ],
        token_estimate=estimate,
        budget_status="estimated" if limit else "unverified",
        input_hash=digest(
            {
                "compiler": 1,
                "preset": preset.model_dump(),
                "config": config.model_dump(),
                "messages": encoded,
                "tools": tools,
            }
        ),
    )
