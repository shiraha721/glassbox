"""Check documentation links and declaration/callback coverage without starting the application."""

import ast
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# TypeScript's own parser identifies named arrows, JSX callbacks and class members precisely.
# It is a locked development dependency; this checker does not install packages or write files.
TS_INVENTORY = r"""
const fs = require('node:fs'), path = require('node:path');
const root = process.argv[1], ts = require(path.join(root, 'web/node_modules/typescript'));
const symbols = [], callbacks = [];
function files(folder) {
  return fs.readdirSync(folder, {withFileTypes: true}).flatMap(entry => {
    const filename = path.join(folder, entry.name);
    return entry.isDirectory() ? files(filename) : /\.tsx?$/.test(entry.name) && entry.name !== 'generated.ts' ? [filename] : [];
  });
}
for (const filename of [...files(path.join(root, 'web/src')), ...files(path.join(root, 'web/e2e'))]) {
  const body = fs.readFileSync(filename, 'utf8');
  const source = ts.createSourceFile(filename, body, ts.ScriptTarget.Latest, true, filename.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const relative = path.relative(root, filename).replaceAll('\\', '/');
  function visit(node, owners = []) {
    let next = owners;
    const line = source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
    if (ts.isClassDeclaration(node) && node.name) {
      next = [...owners, node.name.text];
      symbols.push({path: relative, name: next.join('.'), line});
    } else if (ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || ts.isConstructorDeclaration(node) || ts.isArrowFunction(node) || ts.isFunctionExpression(node)) {
      let name = node.name?.getText(source);
      if (ts.isConstructorDeclaration(node)) name = 'constructor';
      if (!name && ts.isVariableDeclaration(node.parent) && ts.isIdentifier(node.parent.name)) name = node.parent.name.text;
      if (name) {
        next = [...owners, name];
        symbols.push({path: relative, name: next.join('.'), line});
      } else callbacks.push([relative, line]);
    }
    ts.forEachChild(node, child => visit(child, next));
  }
  visit(source);
}
process.stdout.write(JSON.stringify({symbols, callbacks}));
"""


def python_inventory() -> tuple[list[dict[str, Any]], set[tuple[str, int]]]:
    """Read first-party Python declarations and anonymous lambdas from their actual AST positions."""
    symbols, callbacks = [], set()

    def visit(node: ast.AST, path: str, owners: tuple[str, ...] = ()) -> None:
        """Retain class/function ownership for private, decorated and nested declarations."""
        for child in ast.iter_child_nodes(node):
            next_owners = owners
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                next_owners = (*owners, child.name)
                symbols.append({"path": path, "name": ".".join(next_owners), "line": child.lineno})
            if isinstance(child, ast.Lambda):
                callbacks.add((path, child.lineno))
            visit(child, path, next_owners)

    for directory in ("src/rtagent", "tests", "scripts", "migrations"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            visit(
                ast.parse(path.read_text(encoding="utf-8-sig")), path.relative_to(ROOT).as_posix()
            )
    return symbols, callbacks


def typescript_inventory() -> tuple[list[dict[str, Any]], list[tuple[str, int]]]:
    """Use the installed TypeScript parser with Node; surface missing tooling as a setup error."""
    result = subprocess.run(
        ["node", "-e", TS_INVENTORY, str(ROOT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    value = json.loads(result.stdout)
    return value["symbols"], [tuple(item) for item in value["callbacks"]]


def slug(heading: str) -> str:
    """Produce the GitHub-style heading anchor used by this repository's local cross-references."""
    heading = re.sub(r"<[^>]*>", "", heading).lower()
    return re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")


def headings(path: Path) -> set[str]:
    """Index headings outside fenced code, including the suffixes of duplicate headings."""
    result, seen, inside = set(), {}, False
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("```"):
            inside = not inside
        if not inside and (match := re.match(r"^#{1,6}\s+(.+?)\s*#*$", line)):
            name = slug(match[1])
            count = seen.get(name, 0)
            result.add(name + (f"-{count}" if count else ""))
            seen[name] = count + 1
    return result


def check() -> list[str]:
    """Audit links, code fences, reference tables, exact declaration positions and callback coverage."""
    errors, symbol_links, callback_links = [], {}, set()
    links = 0
    files = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").rglob("*.md"))]
    for path in files:
        text = path.read_text(encoding="utf-8-sig")
        if text.count("```") % 2:
            errors.append(f"Unbalanced code fence: {path.relative_to(ROOT)}")
        for match in re.finditer(r"\[([^\]\n]*)\]\(([^)\n]+)\)", text):
            label, target = match.groups()
            if re.match(r"(?:https?://|mailto:|app://|codex:)", target):
                continue
            relative, _, anchor = target.strip("<>").partition("#")
            resolved = (path.parent / relative).resolve() if relative else path
            links += 1
            if not resolved.exists():
                errors.append(f"Missing link: {path.relative_to(ROOT)} -> {target}")
            elif re.fullmatch(r"L\d+", anchor):
                lineno = int(anchor[1:])
                if lineno > len(resolved.read_text(encoding="utf-8-sig").splitlines()):
                    errors.append(f"Invalid line: {path.relative_to(ROOT)} -> {target}")
                repo_path = resolved.relative_to(ROOT).as_posix()
                callback_links.add((repo_path, lineno))
                if label.startswith("`") and label.endswith("`"):
                    symbol_links[repo_path, label.strip("`")] = lineno
            elif anchor and resolved.suffix == ".md" and anchor not in headings(resolved):
                errors.append(f"Missing anchor: {path.relative_to(ROOT)} -> {target}")
        if path.is_relative_to(ROOT / "docs/reference"):
            for number, line in enumerate(text.splitlines(), 1):
                if (
                    line.startswith("| ")
                    and "../../" in line
                    and len(re.findall(r"(?<!\\)\|", line)) not in (4, 5)
                ):
                    errors.append(f"Malformed table row: {path.relative_to(ROOT)}:{number}")

    python_symbols, python_callbacks = python_inventory()
    ts_symbols, ts_callbacks = typescript_inventory()
    for item in python_symbols + ts_symbols:
        key = (item["path"], item["name"])
        if key not in symbol_links:
            errors.append(f"Undocumented named symbol: {key}")
        elif symbol_links[key] != item["line"]:
            errors.append(f'Wrong declaration link: {key}: {symbol_links[key]} != {item["line"]}')
    for path, line in [*python_callbacks, *ts_callbacks]:
        if (path, line) not in callback_links:
            errors.append(f"Undocumented callback: {path}:{line}")
    print(
        f"Documentation: {len(files)} files, {links} local links, {len(python_symbols) + len(ts_symbols)} named declarations, {len(ts_callbacks)} TypeScript callbacks, {len(python_callbacks)} Python lambdas."
    )
    return errors


def main() -> None:
    """Print actionable audit failures and exit without migrating databases or launching services."""
    try:
        errors = check()
    except (OSError, subprocess.CalledProcessError) as error:
        print(
            f"Documentation tooling unavailable ({type(error).__name__}); install Node and run npm --prefix web ci."
        )
        raise SystemExit(1) from None
    for error in errors:
        print("ERROR", error)
    print("Documentation audit:", "FAIL" if errors else "PASS")
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
