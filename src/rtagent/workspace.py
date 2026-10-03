"""Workspace files, explicit project memory, and small on-demand Markdown skills."""

import json
import os
import re
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml

from rtagent.domain import AppError, Message, digest

FILE_LIMIT = 65_536
MEMORY_PATH = ".rtagent/MEMORY.md"
SKILL_ROOT = ".agents/skills"
SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
EXCLUDED_DIRS = {".git", ".venv", "node_modules", "var", "__pycache__"}


def excluded_name(name: str) -> bool:
    """Share credential and runtime exclusions between file tools and the sandbox."""
    name = name.casefold()
    return (
        name in EXCLUDED_DIRS | {".env", "access-token"}
        or (name.startswith(".env.") and name != ".env.example")
        or Path(name).suffix in {".db", ".sqlite", ".sqlite3"}
    )


class Workspace:
    """Keep editable context in ordinary bounded files under one explicit root."""

    def __init__(self, root: Path, data_dir: Path | None = None) -> None:
        """Resolve the root once and serialize local file replacements."""
        self.root = root.resolve()
        self.data_dir = data_dir.resolve() if data_dir else self.root / "var"
        self.bundled_skills = Path(__file__).parent / "skills"
        self.lock = threading.RLock()

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
    def read_path(path: Path) -> str:
        """Decode bounded UTF-8 text without allocating an arbitrarily large file."""
        try:
            with path.open("rb") as handle:
                content = handle.read(FILE_LIMIT + 1)
            if len(content) > FILE_LIMIT:
                raise AppError("tool_output_limit", "File exceeds the 64 KiB text limit.")
            return content.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise AppError(
                "tool_error", "The requested file must exist and contain UTF-8 text."
            ) from exc

    def read(self, path: str) -> dict[str, str]:
        """Return text and its content fingerprint for a later guarded replacement."""
        content = self.read_path(self.path(path))
        return {"path": path, "content": content, "hash": digest(content)}

    def write(self, path: str, content: str, expected_hash: str | None = None) -> dict[str, str]:
        """Create a file, or replace one only after the caller has read its current text."""
        if len(content.encode()) > FILE_LIMIT:
            raise AppError("tool_output_limit", "Text exceeds the 64 KiB file limit.")
        with self.lock:
            target = self.path(path)
            previous = self.read_path(target) if target.exists() else ""
            if (target.exists() and expected_hash != digest(previous)) or (
                not target.exists() and expected_hash not in (None, digest(""))
            ):
                raise AppError(
                    "file_conflict",
                    "The file changed or already exists. Read it before replacing it.",
                    409,
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            # Replace atomically so readers never observe a partially written memory or skill.
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=target.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
            try:
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return {"path": path, "hash": digest(content)}

    def edit(self, path: str, old_text: str, new_text: str) -> dict[str, str]:
        """Replace exactly one literal occurrence; ambiguous edits require a narrower match."""
        with self.lock:
            current = self.read(path)
            if not old_text or current["content"].count(old_text) != 1:
                raise AppError(
                    "edit_conflict", "old_text must match exactly once in the current file.", 409
                )
            return self.write(
                path, current["content"].replace(old_text, new_text, 1), current["hash"]
            )

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

    def memory(self) -> dict[str, str]:
        """Read project memory, treating a missing file as an empty editable document."""
        if not self.path(MEMORY_PATH).exists():
            return {"path": MEMORY_PATH, "content": "", "hash": digest("")}
        return self.read(MEMORY_PATH)

    @staticmethod
    def skill_name(name: str) -> str:
        """Accept a single predictable directory name, never a filesystem path."""
        if len(name) > 64 or not SKILL_NAME.fullmatch(name):
            raise AppError(
                "invalid_skill",
                "Skill names use lowercase letters, numbers, and single hyphens (up to 64 characters).",
            )
        return name

    def _skill(self, path: Path, name: str, builtin: bool) -> dict[str, Any]:
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
            "builtin": builtin,
            "path": f"builtin:{name}" if builtin else path.relative_to(self.root).as_posix(),
            "content": text,
            "hash": digest(text),
        }

    def skills(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Discover metadata without injecting every skill body into model context."""
        found, errors = {}, {}
        local = self.path(SKILL_ROOT)
        for root, builtin in ((self.bundled_skills, True), (local, False)):
            if not root.exists():
                continue
            for directory in sorted(root.iterdir()):
                if not directory.is_dir():
                    continue
                try:
                    name = self.skill_name(directory.name)
                    path = (
                        directory / "SKILL.md"
                        if builtin
                        else self.path(f"{SKILL_ROOT}/{name}/SKILL.md")
                    )
                    if not builtin and name in found:
                        raise AppError(
                            "invalid_skill", "A workspace skill cannot replace a bundled skill."
                        )
                    found[name] = self._skill(path, name, builtin)
                except AppError as exc:
                    errors[directory.name] = exc.message
                if len(found) + len(errors) > 100:
                    raise AppError("skill_limit", "The workspace supports at most 100 skills.")
        return list(found.values()), errors

    def load_skill(self, name: str) -> dict[str, Any]:
        """Resolve an explicitly selected skill and retain its origin and fingerprint."""
        self.skill_name(name)
        skills, errors = self.skills()
        for skill in skills:
            if skill["name"] == name:
                return skill
        raise AppError("skill_unavailable", errors.get(name, f"Skill {name} was not found."))

    def save_skill(
        self, name: str, description: str, instructions: str, expected_hash: str | None = None
    ) -> dict[str, str]:
        """Write a usable workspace SKILL.md; bundled skills remain read-only."""
        self.skill_name(name)
        if (self.bundled_skills / name).exists():
            raise AppError("tool_denied", "Bundled skills cannot be overwritten.", 403)
        if not description.strip() or len(description) > 1000 or not instructions.strip():
            raise AppError(
                "invalid_skill", "Provide a description (up to 1,000 characters) and instructions."
            )
        header = yaml.safe_dump(
            {"name": name, "description": description}, sort_keys=False, allow_unicode=True
        )
        return self.write(
            f"{SKILL_ROOT}/{name}/SKILL.md",
            f"---\n{header}---\n\n{instructions.strip()}\n",
            expected_hash,
        )

    def instructions(self) -> dict[str, str] | None:
        """Read root AGENTS.md for both composition and execution, retaining its fingerprint."""
        return self.read("AGENTS.md") if self.path("AGENTS.md").exists() else None

    def context(self, selected: list[str]) -> list[Message]:
        """Capture literal workspace context once, outside the pure prompt compiler."""
        messages = []
        instructions = self.instructions()
        if instructions:
            messages.append(
                Message(
                    role="system",
                    source="workspace:AGENTS.md",
                    content=instructions["content"],
                )
            )
        memory = self.memory()["content"]
        if memory:
            messages.append(
                Message(
                    role="system",
                    source="workspace:memory",
                    content="Workspace memory (.rtagent/MEMORY.md):\n" + memory,
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
