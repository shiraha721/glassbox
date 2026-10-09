"""Tool permission decisions are data-driven and independent of prompt content."""

import re

from jsonschema import Draft202012Validator

from glassbox.domain import (
    AppError,
    CommandRule,
    GlassboxConfig,
    Grant,
    ToolCall,
    ToolDefinition,
)

SHELL_TOOLS = frozenset({"bash", "powershell"})
# Anything that chains, pipes, redirects or substitutes makes a command more than its prefix.
COMPOUND = re.compile(r"[;&|`<>\n\r]|\$\(")


def simple_command(command: str) -> bool:
    """True for one plain command, with no operators that could run anything after the prefix."""
    return not COMPOUND.search(command)


def starts_with(prefix: str, command: str) -> bool:
    """Match whole words: `npm` covers `npm test` but not `npmx`."""
    words = " ".join(command.split())
    return words == prefix or words.startswith(prefix + " ")


def command_permission(command: str, rules: list[CommandRule]) -> str | None:
    """The permission of the longest matching rule, or None to keep the tool's default."""
    matching = [rule for rule in rules if starts_with(rule.prefix, command)]
    if not matching:
        return None
    rule = max(matching, key=lambda item: len(item.prefix))
    if rule.permission != "deny" and not simple_command(command):
        return None
    return rule.permission


def grant_for(call: ToolCall) -> Grant:
    """The narrowest useful standing approval for this call: a shell program, or the tool."""
    if call.name in SHELL_TOOLS:
        command = str(call.args.get("command", ""))
        words = command.split()
        return Grant(tool=call.name, prefix=words[0] if words else None)
    return Grant(tool=call.name)


def granted(call: ToolCall, grants: list[Grant]) -> Grant | None:
    """The conversation grant that covers this call, if any."""
    for grant in grants:
        if grant.tool != call.name:
            continue
        if call.name not in SHELL_TOOLS:
            return grant
        command = str(call.args.get("command", ""))
        if grant.prefix and simple_command(command) and starts_with(grant.prefix, command):
            return grant
    return None


def decide(call: ToolCall, definition: ToolDefinition | None, config: GlassboxConfig) -> str:
    """Validate arguments and return only a permission present in the frozen profile."""
    if definition is None or call.name not in config.tools:
        return "deny"
    errors = list(Draft202012Validator(definition.input_schema).iter_errors(call.args))
    if errors:
        raise AppError("invalid_tool_arguments", errors[0].message)
    permission = config.tools.get(call.name, "deny")
    if permission != "deny" and call.name in SHELL_TOOLS:
        ruled = command_permission(str(call.args.get("command", "")), config.command_rules)
        if ruled is not None:
            return ruled
    return permission
