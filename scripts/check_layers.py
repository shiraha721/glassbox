"""Check concrete dependency direction without importing the application or starting resources."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_python() -> list[str]:
    """Reject outward domain imports and reverse infrastructure/application dependencies."""
    errors = []
    package = ROOT / "src/rtagent"
    forbidden = {
        "domain": (
            "application",
            "infrastructure",
            "interfaces",
            "harness",
            "main",
            "client",
            "cli",
        ),
        "infrastructure": ("application", "interfaces", "harness", "main", "client", "cli"),
        "application": ("interfaces", "main", "client", "cli", "harness"),
    }
    for path in package.rglob("*.py"):
        layer = path.relative_to(package).parts[0]
        if layer not in forbidden:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            )
            if isinstance(node, ast.ImportFrom) and node.level:
                # Resolve relative imports as well; package aliases must not bypass the rule.
                parents = list(path.relative_to(package).parts[:-1])
                prefix = ["rtagent", *parents[: len(parents) - node.level + 1]]
                names = [".".join([*prefix, name]) for name in names]
            for name in names:
                if any(
                    name == f"rtagent.{target}" or name.startswith(f"rtagent.{target}.")
                    for target in forbidden[layer]
                ):
                    errors.append(f"{path.relative_to(ROOT)}:{node.lineno}: {layer} imports {name}")
                if layer == "domain" and name.split(".")[0] in {
                    "fastapi",
                    "sqlalchemy",
                    "httpx",
                    "mcp",
                    "langgraph",
                    "langchain_core",
                    "langchain_openai",
                }:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: domain imports runtime adapter {name}"
                    )
    return errors


def check_frontend() -> list[str]:
    """Check source imports resolve and shared/API/features never depend on the app composition."""
    errors = []
    source = ROOT / "web/src"
    for path in source.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        owner = path.relative_to(source).parts[0]
        for match in re.finditer(
            r"(?:from\s+|import\s+)[\"'](\.[^\"']+)[\"']", path.read_text(encoding="utf-8-sig")
        ):
            relative = (path.parent / match[1]).resolve()
            candidates = (relative, relative.with_suffix(".ts"), relative.with_suffix(".tsx"))
            target = next((candidate for candidate in candidates if candidate.is_file()), None)
            if target is None:
                errors.append(f"{path.relative_to(ROOT)}: unresolved import {match[1]}")
                continue
            if not target.is_relative_to(source):
                errors.append(f"{path.relative_to(ROOT)}: source import escapes web/src")
                continue
            dependency = target.relative_to(source).parts[0]
            if (owner in {"api", "shared"} and dependency in {"app", "features"}) or (
                owner == "features" and dependency == "app"
            ):
                errors.append(f"{path.relative_to(ROOT)}: {owner} imports {dependency}")
    return errors


def main() -> None:
    """Report concrete import violations; perform no dependency installation or runtime writes."""
    errors = check_python() + check_frontend()
    for error in errors:
        print("ERROR", error)
    print("Layer direction and frontend imports:", "FAIL" if errors else "PASS")
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
