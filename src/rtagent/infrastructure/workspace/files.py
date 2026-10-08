"""Workspace files, explicit project memory, and small on-demand Markdown skills."""

import hashlib
import json
import os
import re
import shutil
import threading
from contextlib import suppress
from difflib import unified_diff
from fnmatch import fnmatchcase
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml

from rtagent.domain import AppError, Message, digest
from rtagent.domain.commands import COMMAND_NAME, expand_template, parse_invocation
from rtagent.domain.execution import current_execution
from rtagent.domain.patches import apply_hunks, parse_patch

FILE_LIMIT = 65_536
TEXT_FILE_LIMIT = 2 * 1024 * 1024
# Memory is studio data, kept per workspace in the data directory. Earlier versions kept it
# in the project at this path; it moves out the first time the workspace's memory is used.
LEGACY_MEMORY_PATH = ".rtagent/MEMORY.md"
SKILL_ROOT = ".agents/skills"
COMMAND_ROOT = ".agents/commands"
SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# Git metadata is private because remote URLs and config can embed credentials.
PRIVATE_NAMES = {".env", ".git", "access-token"}
DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
# Large generated trees: skipped by file tools and Windows workspace copies, but visible to
# Linux commands (a direct bind mount) so project test suites can run offline.
DEPENDENCY_DIRS = {".venv", "node_modules", "__pycache__"}


def private_name(name: str) -> bool:
    """Recognize credentials, Git metadata and databases hidden from every tool and command."""
    name = name.casefold()
    return (
        name in PRIVATE_NAMES
        or (name.startswith(".env.") and name != ".env.example")
        or Path(name).suffix in DATABASE_SUFFIXES
    )


def excluded_name(name: str) -> bool:
    """Recognize paths that file tools, packages and copied sandboxes never include."""
    return private_name(name) or name.casefold() in DEPENDENCY_DIRS


class Workspace:
    """Keep editable context in ordinary bounded files under one explicit root."""

    def __init__(self, root: Path, data_dir: Path | None = None) -> None:
        """Resolve the root once and serialize local file replacements."""
        self.root = root.resolve()
        self.data_dir = data_dir.resolve() if data_dir else self.root / "var"
        self.bundled_skills = Path(__file__).resolve().parents[2] / "skills"
        # User-level skills shared by every project, kept with the studio's own data.
        self.global_skills = data_dir.resolve() / "skills" if data_dir else None
        self.global_commands = data_dir.resolve() / "commands" if data_dir else None
        self.lock = threading.RLock()
        # Content-addressed copies of files a Run replaced, so its changes can be undone.
        self.history_dir = self.data_dir / "file-history"

    def _tracked(self, target: Path) -> bool:
        """Journal changes made inside a recorded tool call to files of this workspace."""
        return (
            current_execution.get() is not None
            and target.is_relative_to(self.root)
            and not target.is_relative_to(self.data_dir)
        )

    def _backup(self, target: Path) -> str | None:
        """Keep the bytes a tool is about to replace; return their hash, or None if absent."""
        if not self._tracked(target) or not target.exists():
            return None
        content = target.read_bytes()
        name = hashlib.sha256(content).hexdigest()
        stored = self.history_dir / name
        if not stored.exists():
            self.history_dir.mkdir(parents=True, exist_ok=True)
            self._write_bytes(stored, content)
        return name

    def record_change(self, target: Path, before: str | None, after: bytes | None) -> None:
        """Add a completed change to the current tool call's receipt."""
        execution = current_execution.get()
        if execution is None or not self._tracked(target):
            return
        execution.changes.append(
            {
                "path": target.relative_to(self.root).as_posix(),
                "before": before,
                "after": None if after is None else hashlib.sha256(after).hexdigest(),
            }
        )

    @staticmethod
    def _write_bytes(target: Path, content: bytes) -> None:
        """Replace a file atomically with exact bytes."""
        with NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def file_state(self, relative: str) -> str | None:
        """Hash a workspace file's bytes, or None when it does not exist."""
        target = self.path(relative)
        return hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None

    def restore(self, relative: str, before: str | None, expected: str | None) -> bool:
        """Put back a file's recorded earlier state if it is still in the `expected` state.

        Returns False, changing nothing, when the file has changed since; an absent `before`
        removes a file the Run created."""
        with self.lock:
            target = self.path(relative)
            if self.file_state(relative) != expected:
                return False
            if before is None:
                target.unlink(missing_ok=True)
                return True
            try:
                content = (self.history_dir / before).read_bytes()
            except OSError as exc:
                raise AppError(
                    "backup_missing", f"The saved copy of {relative} is missing.", 409
                ) from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_bytes(target, content)
            return True

    def path(self, relative: str) -> Path:
        """Reject traversal, links, runtime data, and credential paths before I/O."""
        requested = Path(relative)
        if requested.is_absolute() or ".." in requested.parts or ":" in relative:
            raise AppError(
                "tool_denied", "Paths outside the workspace are denied; use a relative path.", 403
            )
        candidate = self.root
        for part in requested.parts:
            candidate = candidate / part
            if excluded_name(part) or candidate.is_symlink() or candidate.is_junction():
                raise AppError(
                    "tool_denied",
                    "Credential, runtime, dependency, and linked paths are excluded.",
                    403,
                )
        resolved = candidate.resolve()
        if (
            not resolved.is_relative_to(self.root)
            or resolved.is_relative_to(self.data_dir)
            or any(excluded_name(part) for part in resolved.relative_to(self.root).parts)
        ):
            raise AppError("tool_denied", "The path is outside the allowed workspace.", 403)
        return resolved

    @staticmethod
    def read_path(path: Path, limit: int = FILE_LIMIT) -> str:
        """Decode bounded UTF-8 text without allocating an arbitrarily large file."""
        try:
            with path.open("rb") as handle:
                content = handle.read(limit + 1)
            if len(content) > limit:
                raise AppError(
                    "tool_output_limit", f"File exceeds the {limit // 1024} KiB text limit."
                )
            return content.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise AppError(
                "tool_error", "The requested file must exist and contain UTF-8 text."
            ) from exc

    def read(self, path: str) -> dict[str, str]:
        """Return text and its content fingerprint for a later guarded replacement."""
        content = self.read_path(self.path(path))
        return {"path": path, "content": content, "hash": digest(content)}

    def read_lines(self, path: str, start_line: int = 1, line_count: int = 200) -> dict[str, Any]:
        """Read a bounded line range with the full-file hash, allowing navigation through larger files."""
        if start_line < 1 or not 1 <= line_count <= 500:
            raise AppError("invalid_tool_arguments", "Use a positive start_line and 1–500 lines.")
        content = self.read_path(self.path(path), TEXT_FILE_LIMIT)
        lines = content.splitlines(keepends=True)
        selected, size = [], 0
        for line in lines[start_line - 1 : start_line - 1 + line_count]:
            # Bound escaped JSON as well as raw UTF-8, leaving room for metadata.
            encoded_size = len(json.dumps(line, ensure_ascii=False).encode())
            if size + encoded_size > 24_000:
                if not selected:
                    raise AppError(
                        "tool_output_limit", "This single line exceeds the output budget."
                    )
                break
            selected.append(line)
            size += encoded_size
        next_line = start_line + len(selected)
        return {
            "path": path,
            "content": "".join(selected),
            "hash": digest(content),
            "start_line": start_line,
            "end_line": next_line - 1,
            "total_lines": len(lines),
            "next_line": next_line if next_line <= len(lines) else None,
            "truncated": next_line <= len(lines),
        }

    def _walk(self, path: str) -> tuple[list[Path], bool]:
        """Enumerate at most 10,000 entries without descending into excluded or linked directories."""
        directory = self.path(path)
        if not directory.is_dir():
            raise AppError("tool_error", "The search root must be an existing workspace directory.")
        files, pending, visited = [], [directory], 0
        while pending:
            children = []
            try:
                with os.scandir(pending.pop()) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > 10_000:
                            return sorted(files), True
                        children.append(Path(entry.path))
            except OSError as exc:
                raise AppError("tool_error", "A searched directory is inaccessible.") from exc
            for child in sorted(children, reverse=True):
                try:
                    checked = self.path(child.relative_to(self.root).as_posix())
                except AppError:
                    continue
                if checked.is_dir():
                    pending.append(checked)
                elif checked.is_file():
                    files.append(checked)
        return sorted(files), False

    @staticmethod
    def _matches(path: str, pattern: str) -> bool:
        """Match a relative glob, including root files when the pattern starts with **/."""
        return fnmatchcase(path, pattern) or (
            pattern.startswith("**/") and fnmatchcase(path, pattern[3:])
        )

    def search_files(
        self, pattern: str = "**/*", path: str = ".", limit: int = 100
    ) -> dict[str, Any]:
        """Find workspace files by glob with explicit traversal and result truncation."""
        files, truncated = self._walk(path)
        found, size = [], 0
        for file in files:
            relative = file.relative_to(self.root).as_posix()
            if not self._matches(relative, pattern):
                continue
            size += len(json.dumps(relative, ensure_ascii=False).encode())
            if len(found) >= limit or size > 24_000:
                truncated = True
                break
            found.append(relative)
        return {"files": found, "truncated": truncated, "pattern": pattern, "path": path}

    def search_text(
        self,
        query: str,
        path: str = ".",
        pattern: str = "**/*",
        case_sensitive: bool = True,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Search literal UTF-8 text with line numbers, bounded scanning, and explicit skipped files."""
        if not query:
            raise AppError("invalid_tool_arguments", "Enter nonempty literal search text.")
        files, truncated = self._walk(path)
        matches, skipped, scanned, used, size = [], 0, 0, 0, 0
        needle = query if case_sensitive else query.casefold()
        for file in files:
            relative = file.relative_to(self.root).as_posix()
            if not self._matches(relative, pattern):
                continue
            if scanned >= 1000 or used >= 8 * 1024 * 1024:
                truncated = True
                break
            scanned += 1
            try:
                allowance = min(TEXT_FILE_LIMIT, 8 * 1024 * 1024 - used)
                size_on_disk = file.stat().st_size
                if size_on_disk > TEXT_FILE_LIMIT:
                    skipped += 1
                    continue
                used += min(size_on_disk, allowance)
                content = self.read_path(file, allowance)
            except (OSError, AppError):
                skipped += 1
                continue
            if "\0" in content:
                skipped += 1
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if needle not in (line if case_sensitive else line.casefold()):
                    continue
                item = {
                    "path": relative,
                    "line": number,
                    "text": line[:600],
                    "line_truncated": len(line) > 600,
                }
                size += len(json.dumps(item, ensure_ascii=False).encode())
                if len(matches) >= limit or size > 24_000:
                    return {
                        "matches": matches,
                        "truncated": True,
                        "scanned_files": scanned,
                        "skipped_files": skipped,
                    }
                matches.append(item)
        return {
            "matches": matches,
            "truncated": truncated,
            "scanned_files": scanned,
            "skipped_files": skipped,
        }

    def apply_patch(
        self, patch: str, expected_hashes: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Preflight all file operations and hashes before applying per-file atomic changes."""
        operations = parse_patch(patch)
        hashes = expected_hashes or {}
        prepared, seen = [], set()
        with self.lock:
            for operation in operations:
                target = self.path(operation.path)
                identity = os.path.normcase(str(target))
                if identity in seen:
                    raise AppError("invalid_patch", "Two patch paths refer to the same file.")
                seen.add(identity)
                exists = target.exists()
                if operation.action == "add":
                    if exists:
                        raise AppError("file_conflict", "Add File must name a new file.", 409)
                    before, after = "", "\n".join(line[1:] for line in operation.lines)
                    if operation.lines:
                        after += "\n"
                else:
                    before = self.read_path(target, TEXT_FILE_LIMIT)
                    if hashes.get(operation.path) != digest(before):
                        raise AppError(
                            "file_conflict",
                            f"Read {operation.path} and supply its current hash before updating or deleting it.",
                            409,
                        )
                    after = (
                        apply_hunks(before, operation.lines) if operation.action == "update" else ""
                    )
                if len(after.encode()) > TEXT_FILE_LIMIT:
                    raise AppError("tool_output_limit", "A patched file exceeds the 2 MiB limit.")
                difference = "".join(
                    unified_diff(
                        before.splitlines(keepends=True),
                        after.splitlines(keepends=True),
                        fromfile=operation.path,
                        tofile=operation.path,
                    )
                )
                prepared.append((operation, before, after, difference))
            manifest = [
                {
                    "path": operation.path,
                    "operation": operation.action,
                    "hash": digest(after) if operation.action != "delete" else None,
                    "diff": "",
                    "diff_truncated": False,
                }
                for operation, _, after, _ in prepared
            ]
            metadata_size = len(
                json.dumps({"files": manifest, "atomic": False}, ensure_ascii=False).encode()
            )
            if metadata_size > 40_000:
                raise AppError(
                    "tool_output_limit", "Patch path metadata exceeds the result budget."
                )
            diff_budget = (48_000 - metadata_size) // len(manifest)
            for item, (_, _, _, difference) in zip(manifest, prepared, strict=True):
                preview = difference[:4000]
                while len(json.dumps(preview, ensure_ascii=False).encode()) > diff_budget:
                    preview = preview[: len(preview) // 2]
                item.update(diff=preview, diff_truncated=len(preview) < len(difference))
            # Bound the evidence before effects, so successful writes cannot lose their receipt
            # merely because the subsequent serialized output is too large.
            applied = []
            for item, (operation, before, after, _) in zip(manifest, prepared, strict=True):
                try:
                    if operation.action == "delete":
                        target = self.path(operation.path)
                        if digest(self.read_path(target, TEXT_FILE_LIMIT)) != digest(before):
                            raise AppError(
                                "file_conflict", "A file changed after patch preflight.", 409
                            )
                        before = self._backup(target)
                        target.unlink()
                        self.record_change(target, before, None)
                    else:
                        self._replace(
                            operation.path,
                            after,
                            digest(before) if operation.action == "update" else None,
                        )
                except (OSError, AppError) as exc:
                    raise AppError(
                        "partial_patch",
                        "Patch application stopped. Inspect the reported completed files before retrying.",
                        409,
                        {"files": applied, "failed_path": operation.path},
                    ) from exc
                applied.append(item)
        return {"files": applied, "atomic": False}

    def write(self, path: str, content: str, expected_hash: str | None = None) -> dict[str, str]:
        """Create a file, or replace one only after the caller has read its current text."""
        if len(content.encode()) > FILE_LIMIT:
            raise AppError(
                "tool_output_limit",
                "Text exceeds the 64 KiB direct-write limit; use a focused patch for larger files.",
            )
        return self._replace(path, content, expected_hash)

    def _replace(self, path: str, content: str, expected_hash: str | None) -> dict[str, str]:
        """Share guarded atomic replacement while allowing bounded patches to edit larger files."""
        with self.lock:
            self._write_file(self.path(path), content, expected_hash)
        return {"path": path, "hash": digest(content)}

    def _write_file(self, target: Path, content: str, expected_hash: str | None) -> None:
        """Atomically replace a file only if it still has the hash the caller last read."""
        if len(content.encode()) > TEXT_FILE_LIMIT:
            raise AppError("tool_output_limit", "Text exceeds the 2 MiB file limit.")
        with self.lock:
            previous = self.read_path(target, TEXT_FILE_LIMIT) if target.exists() else ""
            if (target.exists() and expected_hash != digest(previous)) or (
                not target.exists() and expected_hash not in (None, digest(""))
            ):
                raise AppError(
                    "file_conflict",
                    "The file changed or already exists. Read it before replacing it.",
                    409,
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            before = self._backup(target)
            # Replace atomically so readers never observe a partially written memory or skill.
            self._write_bytes(target, content.encode())
            self.record_change(target, before, content.encode())

    def edit(self, path: str, old_text: str, new_text: str) -> dict[str, str]:
        """Replace exactly one literal occurrence; ambiguous edits require a narrower match.

        Edits are small by construction, so they apply to any file read_text can read."""
        with self.lock:
            current = self.read_path(self.path(path), TEXT_FILE_LIMIT)
            if not old_text or current.count(old_text) != 1:
                raise AppError(
                    "edit_conflict", "old_text must match exactly once in the current file.", 409
                )
            return self._replace(path, current.replace(old_text, new_text, 1), digest(current))

    def list_files(self, path: str = ".") -> dict[str, Any]:
        """List one directory, leaving recursion to explicit subsequent calls."""
        directory = self.path(path)
        if not directory.is_dir():
            raise AppError("tool_error", "The requested directory does not exist.")
        entries = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            try:
                checked = self.path(child.relative_to(self.root).as_posix())
            except AppError:
                continue
            entries.append({"name": child.name, "directory": checked.is_dir()})
            if len(entries) > 200:
                break
        return {"path": path, "entries": entries[:200], "truncated": len(entries) > 200}

    @property
    def memory_file(self) -> Path:
        """This workspace's memory in the data directory, in a folder named by its root."""
        key = hashlib.sha256(str(self.root).encode()).hexdigest()[:16]
        return self.data_dir / "memory" / key / "MEMORY.md"

    def _memory_target(self) -> Path:
        """The memory file, after moving one an earlier version left in the project."""
        target = self.memory_file
        try:
            legacy = self.path(LEGACY_MEMORY_PATH)  # rejects links and anything outside the root
        except AppError:
            return target
        with self.lock:
            if not target.exists() and legacy.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                # Copy, then remove: the project and the data directory may be on other drives.
                shutil.copy2(legacy, target)
                with suppress(FileNotFoundError):
                    legacy.unlink()
                with suppress(OSError):
                    legacy.parent.rmdir()  # only if nothing else, such as packages, is left
        return target

    def memory(self) -> dict[str, str]:
        """Read workspace memory, treating a missing file as an empty editable document."""
        target = self._memory_target()
        content = self.read_path(target) if target.exists() else ""
        return {"path": str(target), "content": content, "hash": digest(content)}

    def save_memory(self, content: str, expected_hash: str | None = None) -> dict[str, str]:
        """Replace workspace memory only if it still has the hash the caller last read."""
        if len(content.encode()) > FILE_LIMIT:
            raise AppError("tool_output_limit", "Memory is limited to 64 KiB of text.")
        target = self._memory_target()
        with self.lock:
            self._write_file(target, content, expected_hash)
            # Name the workspace beside its memory, so the folder can be recognized.
            (target.parent / "workspace.txt").write_text(f"{self.root}\n", encoding="utf-8")
        return {"path": str(target), "hash": digest(content)}

    @staticmethod
    def skill_name(name: str) -> str:
        """Accept a single predictable directory name, never a filesystem path."""
        if len(name) > 64 or not SKILL_NAME.fullmatch(name):
            raise AppError(
                "invalid_skill",
                "Skill names use lowercase letters, numbers, and single hyphens (up to 64 characters).",
            )
        return name

    def _skill(self, path: Path, name: str, scope: str) -> dict[str, Any]:
        text = self.read_path(path)
        normalized = text.replace("\r\n", "\n")
        frontmatter = re.fullmatch(r"---\n(.*?)\n---\n([\s\S]*)", normalized, re.DOTALL)
        try:
            metadata = yaml.safe_load(frontmatter[1]) if frontmatter else None
        except yaml.YAMLError as exc:
            raise AppError("invalid_skill", f"Invalid YAML in skill {name}.") from exc
        if (
            not isinstance(metadata, dict)
            or metadata.get("name") != name
            or not isinstance(metadata.get("description"), str)
            or not metadata["description"].strip()
        ):
            raise AppError(
                "invalid_skill", f"Skill {name} needs matching name and description frontmatter."
            )
        if len(metadata["description"]) > 1000 or not frontmatter[2].strip():
            raise AppError(
                "invalid_skill",
                f"Skill {name} needs a description under 1,000 characters and a nonempty body.",
            )
        return {
            "name": name,
            "description": metadata["description"],
            "builtin": scope == "builtin",
            "scope": scope,
            "path": (
                f"{scope}:{name}" if scope != "project" else path.relative_to(self.root).as_posix()
            ),
            "content": text,
            "hash": digest(text),
        }

    def skills(self, include_project: bool = True) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Discover metadata without injecting every skill body into model context.

        Names are unique across bundled, global and project skills; a later duplicate is
        reported rather than silently shadowing the earlier one."""
        found, errors = {}, {}
        sources = [
            (self.bundled_skills, "builtin"),
            (self.global_skills, "global"),
            (self.path(SKILL_ROOT) if include_project else None, "project"),
        ]
        for root, scope in sources:
            if root is None or not root.exists():
                continue
            for directory in sorted(root.iterdir()):
                if not directory.is_dir():
                    continue
                try:
                    name = self.skill_name(directory.name)
                    path = (
                        self.path(f"{SKILL_ROOT}/{name}/SKILL.md")
                        if scope == "project"
                        else directory / "SKILL.md"
                    )
                    if name in found:
                        raise AppError(
                            "invalid_skill",
                            f"A {found[name]['scope']} skill with this name already exists.",
                        )
                    found[name] = self._skill(path, name, scope)
                except AppError as exc:
                    errors[directory.name] = exc.message
                if len(found) + len(errors) > 100:
                    raise AppError("skill_limit", "The workspace supports at most 100 skills.")
        return list(found.values()), errors

    def _command(self, path: Path, name: str, scope: str) -> dict[str, Any]:
        """Read one command template and its optional description/argument-hint frontmatter."""
        text = self.read_path(path)
        normalized = text.replace("\r\n", "\n")
        frontmatter = re.fullmatch(r"---\n(.*?)\n?---(?:\n([\s\S]*))?", normalized, re.DOTALL)
        metadata: Any = {}
        if frontmatter:
            try:
                metadata = yaml.safe_load(frontmatter[1]) or {}
            except yaml.YAMLError as exc:
                raise AppError("invalid_command", f"Invalid YAML in command {name}.") from exc
        if not isinstance(metadata, dict):
            raise AppError("invalid_command", f"Command {name} frontmatter must be a mapping.")
        template = (frontmatter[2] or "") if frontmatter else normalized
        if not template.strip():
            raise AppError("invalid_command", f"Command {name} has an empty template.")
        return {
            "name": name,
            "description": str(metadata.get("description") or "")[:1000],
            "argument_hint": str(metadata.get("argument-hint") or "")[:200],
            "scope": scope,
            "template": template,
            "hash": digest(text),
        }

    def commands(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Discover custom slash commands; a project command replaces a global one of its name."""
        found: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        project = self.root / COMMAND_ROOT
        sources = [(self.global_commands, "global"), (project, "project")]
        for root, scope in sources:
            if root is None or not root.is_dir():
                continue
            for file in sorted(root.glob("*.md"))[:100]:
                name = file.stem
                if not COMMAND_NAME.fullmatch(name):
                    errors[file.name] = "Use lowercase letters, digits and single hyphens."
                    continue
                try:
                    path = self.path(f"{COMMAND_ROOT}/{file.name}") if scope == "project" else file
                    found[name] = self._command(path, name, scope)
                    errors.pop(name, None)
                except AppError as exc:
                    errors[name] = exc.message
        return sorted(found.values(), key=lambda item: item["name"]), errors

    def expand_command(self, text: str) -> tuple[str, dict[str, Any] | None]:
        """Replace `/name arguments` with the named template; other input is returned as is.

        Input naming no saved command is left alone, so a literal leading slash still works."""
        invocation = parse_invocation(text)
        if invocation is None:
            return text, None
        name, arguments = invocation
        commands, errors = self.commands()
        command = next((item for item in commands if item["name"] == name), None)
        if command is None:
            if name in errors:
                raise AppError("invalid_command", errors[name])
            return text, None
        use = {
            "name": name,
            "arguments": arguments,
            "scope": command["scope"],
            "hash": command["hash"],
        }
        return expand_template(command["template"], arguments), use

    def load_skill(self, name: str) -> dict[str, Any]:
        """Resolve an explicitly selected skill and retain its origin and fingerprint."""
        self.skill_name(name)
        skills, errors = self.skills()
        for skill in skills:
            if skill["name"] == name:
                return skill
        raise AppError("skill_unavailable", errors.get(name, f"Skill {name} was not found."))

    def save_skill(
        self,
        name: str,
        description: str,
        instructions: str,
        expected_hash: str | None = None,
        scope: str = "project",
    ) -> dict[str, str]:
        """Write a usable project or global SKILL.md; bundled skills remain read-only."""
        self.skill_name(name)
        if (self.bundled_skills / name).exists():
            raise AppError("tool_denied", "Bundled skills cannot be overwritten.", 403)
        if scope == "project" and self.global_skills and (self.global_skills / name).exists():
            raise AppError("file_conflict", f"A global skill named {name} already exists.", 409)
        if not description.strip() or len(description) > 1000 or not instructions.strip():
            raise AppError(
                "invalid_skill", "Provide a description (up to 1,000 characters) and instructions."
            )
        header = yaml.safe_dump(
            {"name": name, "description": description}, sort_keys=False, allow_unicode=True
        )
        content = f"---\n{header}---\n\n{instructions.strip()}\n"
        if len(content.encode()) > FILE_LIMIT:
            raise AppError("tool_output_limit", "A skill is limited to 64 KiB.")
        if scope == "global":
            if self.global_skills is None:
                raise AppError("skill_scope", "Global skills need a data directory.")
            target = self.global_skills / name / "SKILL.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_file(target, content, expected_hash)
            return {"path": f"global:{name}", "hash": digest(content)}
        return self.write(f"{SKILL_ROOT}/{name}/SKILL.md", content, expected_hash)

    def delete_global_skill(self, name: str, expected_hash: str) -> None:
        """Remove a global skill folder after checking the user saw its current content."""
        self.skill_name(name)
        directory = self.global_skills / name if self.global_skills else None
        if directory is None or not (directory / "SKILL.md").is_file():
            raise AppError("not_found", f"Global skill {name} was not found.", 404)
        with self.lock:
            if digest(self.read_path(directory / "SKILL.md")) != expected_hash:
                raise AppError("file_conflict", "The skill changed. Reload it first.", 409)
            shutil.rmtree(directory)

    def read_instructions(self, path: str = ".") -> dict[str, Any]:
        """Read the instruction chain down to a file's parent or an existing workspace directory."""
        target = self.path(path)
        directory = target if target.is_dir() else target.parent
        chain = [
            self.root,
            *reversed(
                [
                    item
                    for item in directory.parents
                    if item.is_relative_to(self.root) and item != self.root
                ]
            ),
        ]
        if directory != self.root:
            chain.append(directory)
        documents, size = [], 0
        for parent in chain:
            for name in ("AGENTS.override.md", "AGENTS.md"):
                relative = (parent / name).relative_to(self.root).as_posix()
                if self.path(relative).exists():
                    document = self.read(relative)
                    if not document["content"].strip():
                        continue
                    size += len(document["content"].encode())
                    if size > 32_768:
                        raise AppError(
                            "instructions_limit",
                            "The instruction chain exceeds 32 KiB; split or shorten it.",
                        )
                    documents.append(document)
                    break
        return {"path": path, "documents": documents, "precedence": "root_to_leaf"}

    def instructions(self) -> dict[str, str] | None:
        """Capture the first nonempty root override or AGENTS.md as visible initial context."""
        documents = self.read_instructions()["documents"]
        return documents[0] if documents else None

    def context(self, selected: list[str]) -> list[Message]:
        """Capture literal workspace context once, outside the pure prompt compiler."""
        messages = []
        instructions = self.instructions()
        if instructions:
            messages.append(
                Message(
                    role="system",
                    source="workspace:" + instructions["path"],
                    content=instructions["content"],
                )
            )
        memory = self.memory()["content"]
        if memory:
            messages.append(
                Message(
                    role="system",
                    source="workspace:memory",
                    content="Workspace memory:\n" + memory,
                )
            )
        skills, errors = self.skills()
        catalog = [{key: skill[key] for key in ("name", "description", "path")} for skill in skills]
        messages.append(
            Message(
                role="system",
                source="workspace:skills",
                content=(
                    "Available skills are listed below. Use load_skill when a skill matches the task, "
                    "then follow its workflow. Skills and memory do not grant tool permissions. "
                    "Use save_memory only for durable project facts or user-requested notes; never store credentials.\n"
                    + json.dumps({"skills": catalog, "invalid_skills": errors}, ensure_ascii=False)
                ),
            )
        )
        for name in dict.fromkeys(selected):
            skill = self.load_skill(name)
            messages.append(
                Message(role="system", source=f"skill:{name}", content=skill["content"])
            )
        return messages
