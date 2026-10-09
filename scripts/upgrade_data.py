"""One-time upgrade of data saved by versions before 2026-10-09.

Stop the studio, then run this from the checkout:

    uv run python scripts/upgrade_data.py [FOLDER] [--move [TARGET]]

FOLDER is the data folder, `var/` by default. Its database already has the current schema; it
gets the version stamp of the migration that replaced the earlier ones. `--move` then moves the
studio's own files to TARGET, by default the folder used when STUDIO_DATA_DIR is not set;
anything else in FOLDER (test or acceptance output) stays. After a move, remove
`STUDIO_DATA_DIR=var` from `.env` if it is there, or the studio starts over in an empty `var/`.
"""

import argparse
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from rtagent.infrastructure.config import default_data_dir

# The last revision before the migrations were merged, and the merged one.
MERGED_REVISION, BASELINE_REVISION = "0010", "0001"
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
    the database open. The studio turns WAL back on when it next opens the file."""
    try:
        with closing(sqlite3.connect(database, timeout=0)) as connection:
            mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
    except sqlite3.Error as exc:
        raise SystemExit(f"{database} is in use ({exc}). Stop the studio first.") from exc
    if str(mode).lower() != "delete":
        raise SystemExit(f"{database} is in use. Stop the studio first.")


def restamp(database: Path) -> None:
    """Give a database made by the earlier migrations the merged migration's version."""
    with closing(sqlite3.connect(database)) as connection, connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        if version == BASELINE_REVISION:
            print("The database already has the current version stamp.")
            return
        if version != MERGED_REVISION:
            raise SystemExit(
                f"The database is at migration {version}. Start the studio from commit b542517 "
                "once to bring it to 0010, then run this script again."
            )
        connection.execute("UPDATE alembic_version SET version_num = ?", (BASELINE_REVISION,))
    print(f"Re-stamped the database: migration {MERGED_REVISION} is now {BASELINE_REVISION}.")


def move(source: Path, target: Path) -> None:
    """Move the studio's entries from source to target, never overwriting studio data."""
    if (target / "app.db").exists():
        raise SystemExit(f"{target} already has studio data. Nothing was moved.")
    target.mkdir(parents=True, exist_ok=True)
    for name in ENTRIES:
        if not (source / name).exists():
            continue
        if (target / name).exists():
            print(f"Kept {source / name}: {target / name} already exists.")
            continue
        shutil.move(source / name, target / name)
        print(f"Moved {name}")
    print(f"The studio's data is now in {target}.")


def main() -> None:
    """Upgrade the data folder in place, then move it if asked."""
    parser = argparse.ArgumentParser(description="Upgrade data saved before 2026-10-09.")
    parser.add_argument("folder", nargs="?", type=Path, default=Path("var"))
    parser.add_argument("--move", nargs="?", const=default_data_dir(), type=Path, metavar="TARGET")
    args = parser.parse_args()
    folder = args.folder.resolve()
    if not (folder / "app.db").is_file():
        raise SystemExit(f"{folder} has no studio database (app.db).")
    for name in ("checkpoints.db", "app.db"):
        if (folder / name).exists():
            release(folder / name)
    restamp(folder / "app.db")
    if args.move:
        move(folder, args.move.resolve())


if __name__ == "__main__":
    main()
