"""Custom slash commands: saved prompt templates invoked as `/name arguments`."""

import re

COMMAND_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
INVOCATION = re.compile(r"/([a-z0-9]+(?:-[a-z0-9]+)*)(?:[ \t]+([\s\S]*))?")
PLACEHOLDER = re.compile(r"\$(ARGUMENTS|[1-9])")


def parse_invocation(text: str) -> tuple[str, str] | None:
    """Split `/name arguments` typed at the very start of the input; None for other input."""
    match = INVOCATION.fullmatch(text.rstrip())
    if match is None:
        return None
    return match[1], (match[2] or "").strip()


def expand_template(template: str, arguments: str) -> str:
    """Fill `$ARGUMENTS` (all arguments) and `$1`…`$9` (whitespace-separated words).

    Missing positional words become empty. Arguments given to a template without
    placeholders are appended after a blank line, so they are never silently dropped."""
    words = arguments.split()
    used = False

    def fill(match: re.Match[str]) -> str:
        nonlocal used
        used = True
        if match[1] == "ARGUMENTS":
            return arguments
        index = int(match[1]) - 1
        return words[index] if index < len(words) else ""

    expanded = PLACEHOLDER.sub(fill, template).strip()
    if arguments and not used:
        expanded = f"{expanded}\n\n{arguments}"
    return expanded
