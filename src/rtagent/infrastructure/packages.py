"""Reuse prompt and Skill data without importing execution settings or running package code."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from rtagent.domain import AppError, Preset, PresetPackage, SkillPackage, uid
from rtagent.infrastructure.workspace.files import SKILL_ROOT, Workspace, excluded_name


def import_preset(package: PresetPackage) -> Preset:
    """Assign fresh aggregate/block identities so imported data cannot overwrite an existing preset."""
    return package.preset.model_copy(
        update={
            "id": uid(),
            "revision": 0,
            "blocks": [
                block.model_copy(update={"id": uid()}, deep=True) for block in package.preset.blocks
            ],
        },
        deep=True,
    )


def export_skill(workspace: Workspace, name: str) -> SkillPackage:
    """Export one validated Skill and supporting UTF-8 files, rejecting links and excluded names."""
    entry = workspace.load_skill(name)
    directory = (
        workspace.bundled_skills / name
        if entry["builtin"]
        else workspace.path(f"{SKILL_ROOT}/{name}")
    )
    files = {}
    for root, dirs, names in os.walk(directory, followlinks=False):
        for filename in [*dirs, *names]:
            path = Path(root) / filename
            if path.is_symlink() or path.is_junction() or excluded_name(filename):
                raise AppError(
                    "skill_package_path",
                    "Skill packages cannot contain links, credentials or runtime paths.",
                )
            if path.is_file():
                files[path.relative_to(directory).as_posix()] = workspace.read_path(path)
            if len(files) > 50:
                raise AppError("skill_package_limit", "Skill packages support at most fifty files.")
    return SkillPackage(name=name, files=files)


def import_skill(workspace: Workspace, package: SkillPackage) -> dict:
    """Preflight paths/frontmatter then atomically publish a new directory; never overwrite Skills."""
    name = workspace.skill_name(package.name)
    if (workspace.bundled_skills / name).exists():
        raise AppError("tool_denied", "Bundled skills cannot be replaced.", 403)
    target = workspace.path(f"{SKILL_ROOT}/{name}")
    destinations: set[str] = set()
    for relative in package.files:
        if not relative or "\\" in relative or relative.startswith("/"):
            raise AppError("skill_package_path", "Use relative forward-slash file paths.")
        if any(
            not part
            or part in {".", ".."}
            or part.endswith((".", " "))
            or part.split(".")[0].upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *[f"COM{i}" for i in range(1, 10)],
                *[f"LPT{i}" for i in range(1, 10)],
            }
            for part in relative.split("/")
        ):
            raise AppError(
                "skill_package_path",
                "Package filenames must not contain traversal or Windows device/alias names.",
            )
        path = workspace.path(f"{SKILL_ROOT}/{name}/{relative}")
        if not path.is_relative_to(target) or str(path).casefold() in destinations:
            raise AppError(
                "skill_package_path", "Package paths must be distinct files inside the Skill."
            )
        destinations.add(str(path).casefold())
        for other in destinations - {str(path).casefold()}:
            if other.startswith(str(path).casefold() + os.sep) or str(path).casefold().startswith(
                other + os.sep
            ):
                raise AppError("skill_package_path", "A package file cannot also be a directory.")
    with workspace.lock:
        if target.exists():
            raise AppError(
                "file_conflict",
                "Skill already exists; import a new name or edit it with hashes.",
                409,
            )
        staging_parent = workspace.path(".rtagent")
        staging_parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="skill-import-", dir=staging_parent) as temporary:
            staging = Path(temporary)
            for relative, text in package.files.items():
                path = staging / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(text.encode())
            workspace._skill(staging / "SKILL.md", name, True)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Rename a sibling filesystem directory only after complete data validation.
            os.rename(staging, target)
    return workspace.load_skill(name)
