"""Net workspace file changes of a Run, folded from its tool receipts."""

from typing import Any


def net_changes(entries: list[dict[str, Any]]) -> dict[str, tuple[str | None, str | None]]:
    """Map each path to (state before the Run, state the Run left), in first-touch order.

    Entries are the file tools' records in execution order. A path the Run changed and then
    put back exactly is left out, so it is never offered for undo."""
    first: dict[str, str | None] = {}
    last: dict[str, str | None] = {}
    for entry in entries:
        first.setdefault(entry["path"], entry["before"])
        last[entry["path"]] = entry["after"]
    return {path: (before, last[path]) for path, before in first.items() if before != last[path]}
