"""Parse bounded workspace patches and apply exact hunks without filesystem effects."""

from dataclasses import dataclass
from typing import Literal

from glassbox.domain import AppError


@dataclass(frozen=True)
class PatchOperation:
    """One file operation whose paths and hashes are checked by the workspace adapter."""

    path: str
    action: Literal["add", "update", "delete"]
    lines: tuple[str, ...]


def parse_patch(patch: str) -> list[PatchOperation]:
    """Parse Add/Update/Delete headers, rejecting malformed or duplicate file operations."""
    lines = patch.replace("\r\n", "\n").splitlines()
    if len(patch.encode()) > 65_536 or not lines:
        raise AppError("invalid_patch", "A patch must contain at most 64 KiB of UTF-8 text.")
    if lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise AppError("invalid_patch", "Wrap the patch in *** Begin Patch and *** End Patch.")
    result = []
    index = 1
    headers = {
        "*** Add File: ": "add",
        "*** Update File: ": "update",
        "*** Delete File: ": "delete",
    }
    while index < len(lines) - 1:
        header = next((prefix for prefix in headers if lines[index].startswith(prefix)), None)
        if header is None:
            raise AppError(
                "invalid_patch", "Expected an Add File, Update File, or Delete File header."
            )
        path = lines[index][len(header) :].strip()
        if not path or any(item.path == path for item in result):
            raise AppError(
                "invalid_patch", "Each patch path must be nonempty and appear only once."
            )
        index += 1
        body = []
        while index < len(lines) - 1 and (
            not lines[index].startswith("*** ") or lines[index] == "*** End of File"
        ):
            body.append(lines[index])
            index += 1
        action = headers[header]
        while action == "update" and body and not body[-1]:
            body.pop()  # A blank separator before the next header is not hunk context.
        if action == "add" and any(not line.startswith("+") for line in body):
            raise AppError("invalid_patch", "Every added file line must start with +.")
        if action == "delete" and body:
            raise AppError("invalid_patch", "Delete File takes no hunk body.")
        if action == "update" and (not body or sum(line.startswith("@@") for line in body) > 200):
            raise AppError("invalid_patch", "Update File needs 1–200 exact hunks.")
        result.append(PatchOperation(path, action, tuple(body)))
        if len(result) > 20:
            raise AppError("invalid_patch", "A patch may change at most 20 files.")
    if not result:
        raise AppError("invalid_patch", "The patch contains no file operations.")
    return result


def apply_hunks(original: str, lines: tuple[str, ...]) -> str:
    """Apply ordered exact line hunks; missing or ambiguous contexts fail without fuzzy edits."""
    newline = "\r\n" if "\r\n" in original else "\n"
    raw_lines = original.splitlines(keepends=True)
    source = [line.removesuffix("\n").removesuffix("\r") for line in raw_lines]
    output, old, new = [], [], []
    cursor = 0
    at_end = False
    started = False

    def flush() -> None:
        """Resolve one hunk against the unmodified source before appending its replacement."""
        nonlocal cursor
        if not old:
            raise AppError(
                "patch_conflict", "An update hunk needs unchanged or removed context lines.", 409
            )
        matches = []
        for index in range(cursor, len(source) - len(old) + 1):
            if source[index] != old[0] or (at_end and index + len(old) != len(source)):
                continue
            if source[index : index + len(old)] == old:
                matches.append(index)
                if len(matches) == 2:
                    break
        if len(matches) != 1:
            raise AppError(
                "patch_conflict", "Hunk context must match exactly once; add more context.", 409
            )
        index = matches[0]
        output.extend(raw_lines[cursor:index])
        replacement = [
            raw_lines[index + offset] if offset is not None else text + newline
            for text, offset in new
        ]
        if replacement and index + len(old) == len(source) and not original.endswith("\n"):
            replacement[-1] = replacement[-1].removesuffix("\n").removesuffix("\r")
        for number in range(len(replacement) - 1):
            if not replacement[number].endswith("\n"):
                replacement[number] += newline
        output.extend(replacement)
        cursor = index + len(old)

    for line in lines:
        if line.startswith("@@"):
            if started:
                flush()
            old, new, at_end, started = [], [], False, True
        elif line == "*** End of File" and started:
            at_end = True
        elif started and not at_end and (not line or line[0] in " +-"):
            # Models often drop the leading space of a blank context line; treat "" as " ".
            marker, text = (line[0], line[1:]) if line else (" ", "")
            if marker != "+":
                old.append(text)
            if marker != "-":
                new.append((text, len(old) - 1 if marker == " " else None))
        else:
            raise AppError(
                "invalid_patch",
                "Update hunks start with @@; prefix every body line with space, +, or -.",
            )
    if not started:
        raise AppError("invalid_patch", "Update hunks must start with @@.")
    flush()
    output.extend(raw_lines[cursor:])
    # Unchanged lines retain their original endings, including mixed endings and a missing final LF.
    return "".join(output)
