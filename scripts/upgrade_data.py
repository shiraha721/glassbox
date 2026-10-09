"""One-time: move studio data out of `var/`, where versions before 2026-10-08 kept it.

Stop the studio, then run this from the checkout:

    uv run python scripts/upgrade_data.py [SOURCE] [--to TARGET]

SOURCE defaults to `var/`, TARGET to the folder the studio uses when STUDIO_DATA_DIR is not set.
Only the studio's own files move; anything else in SOURCE (test or acceptance output) stays.
Afterwards remove `STUDIO_DATA_DIR=var` from `.env` if it is there, or the studio starts over in
an empty `var/`.
"""

import argparse
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from rtagent.infrastructure.config import default_data_dir

# app.db moves last, so a target that has it holds a complete move.
ENTRIES = (
    "credentials.json",
    "skills",
    "commands",
    "memory",
    "file-history",
    "attachments",
    "checkpoints.db",
    "app.db",
)


def release(database: Path) -> None:
    """Fold a database's write-ahead log into its file, so it moves as one file.

    Leaving WAL mode needs the only connection, which also proves that no running studio has
    the database open. The studio turns WAL back on when it opens the moved file."""
    try:
        with closing(sqlite3.connect(database, timeout=0)) as connection:
            mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
    except sqlite3.Error as exc:
        raise SystemExit(f"{database} is in use ({exc}). Stop the studio first.") from exc
    if str(mode).lower() != "delete":
        raise SystemExit(f"{database} is in use. Stop the studio first.")


def main() -> None:
    """Move the studio's entries from SOURCE to TARGET, refusing to overwrite studio data."""
    parser = argparse.ArgumentParser(description="Move studio data out of var/.")
    parser.add_argument("source", nargs="?", type=Path, default=Path("var"))
    parser.add_argument("--to", dest="target", type=Path, default=default_data_dir())
    args = parser.parse_args()
    source, target = args.source.resolve(), args.target.resolve()
    if not (source / "app.db").is_file():
        raise SystemExit(f"{source} has no studio database (app.db).")
    if (target / "app.db").exists():
        raise SystemExit(f"{target} already has studio data. Nothing was moved.")
    present = [name for name in ENTRIES if (source / name).exists()]
    for name in ("checkpoints.db", "app.db"):
        if name in present:
            release(source / name)
    target.mkdir(parents=True, exist_ok=True)
    for name in present:
        if (target / name).exists():
            print(f"Kept {source / name}: {target / name} already exists.")
            continue
        shutil.move(source / name, target / name)
        print(f"Moved {name}")
    print(f"The studio's data is now in {target}.")


if __name__ == "__main__":
    main()
