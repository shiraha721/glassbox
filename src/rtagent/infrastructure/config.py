"""Process settings. Model credentials are resolved only at the provider boundary."""

import logging
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from rtagent.domain import (
    SHELL_TOOL,
    SIMULATED,
    ProviderConfig,
    ProviderProfile,
    StudioConfig,
    resolve_model,
)

logger = logging.getLogger(__name__)

# Where earlier versions kept studio data: `var/` under the directory the studio started in.
LEGACY_DATA_DIR = Path("var")
# The studio's own entries in a data directory. app.db moves last, so a target that has it
# holds a complete move; anything else in the old folder (test or acceptance output) stays.
DATA_ENTRIES = (
    "credentials.json",
    "skills",
    "commands",
    "memory",
    "file-history",
    "attachments",
    "checkpoints.db",
    "app.db",
)

DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}


def resolve_secret(reference: str) -> str:
    """Resolve an environment reference at execution time, with a local .env fallback."""
    if not reference:
        return ""
    if not reference.isidentifier() or not reference.isascii():
        raise ValueError("Credential references must be environment variable names.")
    return os.environ.get(reference) or dotenv_values(".env").get(reference) or ""


def default_data_dir() -> Path:
    """The per-user folder for studio data, outside any project or source checkout.

    %LOCALAPPDATA%\\rtagent on Windows, ~/Library/Application Support/rtagent on macOS, and
    $XDG_DATA_HOME/rtagent (by default ~/.local/share/rtagent) elsewhere, including WSL."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "rtagent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "rtagent"
    base = os.environ.get("XDG_DATA_HOME", "")
    return (Path(base) if Path(base).is_absolute() else Path.home() / ".local" / "share") / (
        "rtagent"
    )


def release_database(path: Path) -> None:
    """Fold a closed SQLite database's write-ahead log back into the file, so it moves alone.

    Leaving WAL mode needs the only connection to the database, so this also proves that no
    other process (such as a studio still running from the old folder) has it open. The
    studio turns WAL back on when it opens the moved file."""
    try:
        with closing(sqlite3.connect(path, timeout=0)) as database:
            mode = database.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
    except sqlite3.Error as exc:
        raise RuntimeError(f"{path} is open in another process ({exc}).") from exc
    if str(mode).lower() != "delete":
        raise RuntimeError(f"{path} is open in another process.")


def move_legacy_data(legacy: Path, target: Path) -> list[str]:
    """Move studio data an earlier version kept in `legacy` to `target`, once.

    Nothing happens unless `legacy` holds a database and `target` does not. The move refuses
    to start while a database is open elsewhere, and returns the names it moved."""
    if legacy.resolve() == target.resolve() or not (legacy / "app.db").is_file():
        return []
    if (target / "app.db").exists():
        logger.warning("Using %s; the older studio data in %s is not used.", target, legacy)
        return []
    present = [name for name in DATA_ENTRIES if (legacy / name).exists()]
    for name in ("checkpoints.db", "app.db"):
        if name in present:
            release_database(legacy / name)
    target.mkdir(parents=True, exist_ok=True)
    moved = []
    for name in present:
        if (target / name).exists():
            logger.warning("Not moving %s: %s already exists.", legacy / name, target / name)
            continue
        shutil.move(str(legacy / name), str(target / name))
        moved.append(name)
    (legacy / "MOVED.txt").write_text(
        f"Studio data moved to {target}.\nSet STUDIO_DATA_DIR to choose another folder.\n",
        encoding="utf-8",
    )
    return moved


class Settings(BaseSettings):
    """Local process configuration kept outside versioned Run settings."""

    model_config = SettingsConfigDict(env_prefix="STUDIO_", env_file=".env", extra="ignore")

    # Explicit values (STUDIO_DATA_DIR, .env, code) are used as given; see initialize().
    data_dir: Path = Field(default_factory=default_data_dir)
    workspace_root: Path = Path(".")
    provider: Literal["openai", "fake", "anthropic"] | None = None
    openai_base_url: str | None = Field(default=None, validation_alias="OPENAI_BASE_URL")
    openai_model: str | None = Field(default=None, validation_alias="OPENAI_MODEL")
    anthropic_base_url: str | None = Field(default=None, validation_alias="ANTHROPIC_BASE_URL")
    anthropic_model: str | None = Field(default=None, validation_alias="ANTHROPIC_MODEL")
    # Additional browser origins; pages served by this process are always same-origin.
    allowed_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]

    def initialize(self) -> None:
        """Resolve local paths and create the runtime data directory.

        With the default data directory, data that an earlier version kept in `var/` under
        the start directory is moved there first, unless it is still open elsewhere."""
        # Read before assigning below: an assignment marks the field as explicitly set.
        chosen = "data_dir" in self.model_fields_set
        self.data_dir = self.data_dir.resolve()
        self.workspace_root = self.workspace_root.resolve()
        if not chosen:
            legacy = LEGACY_DATA_DIR.resolve()
            try:
                moved = move_legacy_data(legacy, self.data_dir)
            except RuntimeError as exc:
                raise RuntimeError(
                    f"Studio data in {legacy} could not be moved to {self.data_dir}: {exc} "
                    "Stop the other studio and start again, or set STUDIO_DATA_DIR to keep "
                    "using the old folder."
                ) from exc
            if moved:
                logger.warning(
                    "Moved studio data (%s) from %s to %s.", ", ".join(moved), legacy, self.data_dir
                )
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def apply_provider(self, config: StudioConfig) -> StudioConfig:
        """Normalize saved settings on startup and apply explicit .env provider values.

        OPENAI_* and ANTHROPIC_* values update (or create) the `openai` and `anthropic`
        providers. STUDIO_PROVIDER, or the only provider configured in .env, becomes the
        default model."""
        values = config.model_copy(deep=True)
        # A data directory created on another OS names that OS's shell. Carry its permission
        # over to this OS's shell; an explicit permission for the native shell wins. Only
        # current settings change; stored Run snapshots keep their original tool names.
        foreign = "bash" if SHELL_TOOL == "powershell" else "powershell"
        if foreign in values.tools:
            permission = values.tools.pop(foreign)
            values.tools.setdefault(SHELL_TOOL, permission)
        explicit: dict[str, str | None] = {}
        for adapter, base_url, model in (
            ("openai", self.openai_base_url, self.openai_model),
            ("anthropic", self.anthropic_base_url, self.anthropic_model),
        ):
            if base_url is None and model is None:
                continue
            profile = next((item for item in values.providers if item.id == adapter), None)
            if profile is None:
                profile = ProviderProfile(
                    id=adapter,
                    name="Anthropic" if adapter == "anthropic" else "OpenAI-compatible",
                    adapter=adapter,
                    base_url=base_url or DEFAULT_BASE_URLS[adapter],
                    credential_ref=f"{adapter.upper()}_API_KEY",
                    max_output_tokens=32000 if adapter == "anthropic" else 8192,
                )
                values.providers.append(profile)
            if base_url:
                profile.base_url = base_url
            if model:
                profile.models = sorted({*profile.models, model})
            explicit[adapter] = model
        chosen = self.provider or next(iter(explicit), None)
        current = values.provider
        if chosen == "fake":
            values.provider = ProviderConfig(
                profile_id=SIMULATED, adapter="fake", model="simulated"
            )
        elif chosen is not None:
            profile = next((item for item in values.providers if item.id == chosen), None)
            if profile is None:
                profile = ProviderProfile(
                    id=chosen,
                    name="Anthropic" if chosen == "anthropic" else "OpenAI-compatible",
                    adapter=chosen,
                    base_url=DEFAULT_BASE_URLS[chosen],
                    credential_ref=f"{chosen.upper()}_API_KEY",
                )
                values.providers.append(profile)
            model = explicit.get(chosen) or (current.model if current.profile_id == chosen else "")
            if not model and profile.models:
                model = profile.models[0]
            effort = current.reasoning_effort if current.profile_id == chosen else None
            values.provider = ProviderConfig(
                profile_id=chosen, adapter=profile.adapter, model=model, reasoning_effort=effort
            )
        values.provider = resolve_model(values)
        return StudioConfig.model_validate(values.model_dump())

    @property
    def database_url(self) -> str:
        """Build an absolute SQLite URL compatible with Windows paths."""
        return "sqlite+aiosqlite:///" + (self.data_dir.resolve() / "app.db").as_posix()
