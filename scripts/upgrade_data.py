"""One-time upgrade of data and settings saved before 2026-10-09.

On that day the project was renamed from rtagent (Security Agent Studio) to Glassbox and its
database migrations were merged into one. Stop the running server, then run this from the
checkout:

    uv run python scripts/upgrade_data.py [FOLDER] [--move [TARGET]]

- The database in FOLDER (`var/` by default) already has the current schema; it gets the version
  stamp of the merged migration.
- Presets that tell the agent to install packages in `.rtagent/packages` name `.glassbox/packages`.
  Run snapshots keep the text they recorded.
- STUDIO_* settings in `.env` become GLASSBOX_*; their values are left as they are.
- `--move` then moves Glassbox's own files to TARGET, by default the folder used when
  GLASSBOX_DATA_DIR is not set; anything else in FOLDER (test or acceptance output) stays. Remove
  `GLASSBOX_DATA_DIR=var` from `.env` afterwards, or Glassbox starts over in an empty `var/`.
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from glassbox.infrastructure.config import default_data_dir

# The last revision before the migrations were merged, and the merged one.
MERGED_REVISION, BASELINE_REVISION = "0010", "0001"
OLD_PACKAGES, NEW_PACKAGES = ".rtagent/packages", ".glassbox/packages"
OLD_SETTING = re.compile(r"^(\s*(?:#\s*)?(?:export\s+)?)STUDIO_")
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

    Leaving WAL mode needs the only connection, which also proves that no running server has
    the database open. Glassbox turns WAL back on when it next opens the file."""
    try:
        with closing(sqlite3.connect(database, timeout=0)) as connection:
            mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
    except sqlite3.Error as exc:
        raise SystemExit(f"{database} is in use ({exc}). Stop the server first.") from exc
    if str(mode).lower() != "delete":
        raise SystemExit(f"{database} is in use. Stop the server first.")


def restamp(database: Path) -> None:
    """Give a database made by the earlier migrations the merged migration's version."""
    with closing(sqlite3.connect(database)) as connection, connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        if version == BASELINE_REVISION:
            print("The database already has the current version stamp.")
            return
        if version != MERGED_REVISION:
            raise SystemExit(
                f"The database is at migration {version}. Start the version at commit b542517 "
                "once (`uv run rtagent serve`) to bring it to 0010, then run this script again."
            )
        connection.execute("UPDATE alembic_version SET version_num = ?", (BASELINE_REVISION,))
    print(f"Re-stamped the database: migration {MERGED_REVISION} is now {BASELINE_REVISION}.")


def rename_package_folder(database: Path) -> None:
    """Point presets at the renamed package folder, as a new revision of each changed preset."""
    changed = []
    with closing(sqlite3.connect(database)) as connection, connection:
        for identifier, raw in connection.execute("SELECT id, data FROM presets").fetchall():
            preset = json.loads(raw)
            blocks = preset["blocks"]
            for block in blocks:
                block["template"] = block["template"].replace(OLD_PACKAGES, NEW_PACKAGES)
            if blocks != json.loads(raw)["blocks"]:
                preset["revision"] += 1
                connection.execute(
                    "UPDATE presets SET data = ? WHERE id = ?", (json.dumps(preset), identifier)
                )
                changed.append(identifier)
    if changed:
        print(f"Presets now name {NEW_PACKAGES}: {', '.join(changed)}.")


def rename_settings(env: Path) -> None:
    """Rename STUDIO_* settings in .env to GLASSBOX_*, keeping every value and comment."""
    if not env.is_file():
        return
    lines = env.read_text(encoding="utf-8").splitlines(keepends=True)
    renamed = [OLD_SETTING.sub(r"\1GLASSBOX_", line) for line in lines]
    count = sum(old != new for old, new in zip(lines, renamed, strict=True))
    if count:
        env.write_text("".join(renamed), encoding="utf-8")
        print(f"Renamed {count} STUDIO_* lines in {env} to GLASSBOX_*.")


def move(source: Path, target: Path) -> None:
    """Move Glassbox's entries from source to target, never overwriting existing data."""
    if (target / "app.db").exists():
        raise SystemExit(f"{target} already has Glassbox data. Nothing was moved.")
    target.mkdir(parents=True, exist_ok=True)
    for name in ENTRIES:
        if not (source / name).exists():
            continue
        if (target / name).exists():
            print(f"Kept {source / name}: {target / name} already exists.")
            continue
        shutil.move(source / name, target / name)
        print(f"Moved {name}")
    print(f"Glassbox's data is now in {target}.")


def main() -> None:
    """Upgrade the data folder in place and the settings in .env, then move the data if asked."""
    parser = argparse.ArgumentParser(description="Upgrade data saved before 2026-10-09.")
    parser.add_argument("folder", nargs="?", type=Path, default=Path("var"))
    parser.add_argument("--move", nargs="?", const=default_data_dir(), type=Path, metavar="TARGET")
    args = parser.parse_args()
    folder = args.folder.resolve()
    if not (folder / "app.db").is_file():
        raise SystemExit(f"{folder} has no Glassbox database (app.db).")
    for name in ("checkpoints.db", "app.db"):
        if (folder / name).exists():
            release(folder / name)
    restamp(folder / "app.db")
    rename_package_folder(folder / "app.db")
    rename_settings(Path(".env"))
    if args.move:
        move(folder, args.move.resolve())
    leftover = sorted(name for name in os.environ if name.startswith("STUDIO_"))
    if leftover:
        print(
            f"This shell still sets {', '.join(leftover)}. Load .env again, or set the "
            "GLASSBOX_ names, before starting Glassbox."
        )


if __name__ == "__main__":
    main()
