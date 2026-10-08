"""Check that relative links, images and heading anchors in the project documentation resolve."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")  # also matches ![image](...)
HEADING = re.compile(r"#{1,6}\s+(.+?)\s*#*\s*$")


def markdown_lines(path: Path) -> list[str]:
    """Return lines outside fenced code blocks, where links and headings are real."""
    lines, fenced = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced:
            lines.append(line)
    return lines


def anchors(path: Path) -> set[str]:
    """Compute GitHub-style heading anchors, numbering repeated headings."""
    found, seen = set(), {}
    for line in markdown_lines(path):
        match = HEADING.match(line)
        if not match:
            continue
        text = re.sub(r"[`*_]", "", match[1]).strip().lower()
        slug = re.sub(r"[^\w\- ]", "", text).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        found.add(slug if count == 0 else f"{slug}-{count}")
    return found


def problems(path: Path) -> list[str]:
    """List links in one file whose target file or heading does not exist."""
    result = []
    for line in markdown_lines(path):
        for target in LINK.findall(line):
            if re.match(r"[a-z][a-z0-9+.-]*:", target):  # http:, https:, mailto: ...
                continue
            location, _, anchor = target.partition("#")
            destination = (path.parent / location).resolve() if location else path
            if not destination.exists():
                result.append(f"{path.relative_to(ROOT)}: missing {target}")
            elif anchor and destination.suffix == ".md" and anchor not in anchors(destination):
                result.append(f"{path.relative_to(ROOT)}: missing anchor {target}")
    return result


def main() -> int:
    """Check the root entry points and everything under docs/."""
    files = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").rglob("*.md"))]
    failures = [problem for path in files for problem in problems(path)]
    for failure in failures:
        print(failure)
    print(f"Documentation links: {len(files)} files, {'FAIL' if failures else 'PASS'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
