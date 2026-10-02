"""Tool permission decisions are data-driven and independent of prompt content."""

from jsonschema import Draft202012Validator

from rtagent.domain import AppError, StudioConfig, ToolCall, ToolDefinition


def decide(call: ToolCall, definition: ToolDefinition | None, config: StudioConfig) -> str:
    """Validate arguments and return only a permission present in the frozen profile."""
    if definition is None or call.name not in config.tools:
        return "deny"
    errors = list(Draft202012Validator(definition.input_schema).iter_errors(call.args))
    if errors:
        raise AppError("invalid_tool_arguments", errors[0].message)
    return config.tools.get(call.name, "deny")
