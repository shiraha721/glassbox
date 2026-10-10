"""Process settings. Model credentials are resolved only at the provider boundary."""

import os
import sys
from pathlib import Path
from typing import Any, Literal, Self

from dotenv import dotenv_values
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from glassbox.domain import (
    SHELL_TOOL,
    SIMULATED,
    GlassboxConfig,
    ProviderConfig,
    ProviderProfile,
    resolve_model,
)

# The providers that OPENAI_* and ANTHROPIC_* values in .env configure.
EnvProvider = Literal["openai", "anthropic"]

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
    """The per-user folder for Glassbox's data, outside any project or source checkout.

    %LOCALAPPDATA%\\glassbox on Windows, ~/Library/Application Support/glassbox on macOS, and
    $XDG_DATA_HOME/glassbox (by default ~/.local/share/glassbox) elsewhere, including WSL."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "glassbox"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "glassbox"
    base = os.environ.get("XDG_DATA_HOME", "")
    return (Path(base) if Path(base).is_absolute() else Path.home() / ".local" / "share") / (
        "glassbox"
    )


class Settings(BaseSettings):
    """Local process configuration kept outside versioned Run settings."""

    model_config = SettingsConfigDict(env_prefix="GLASSBOX_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=default_data_dir)
    workspace_root: Path = Path(".")
    provider: Literal["openai", "fake", "anthropic"] | None = None
    openai_base_url: str | None = Field(default=None, validation_alias="OPENAI_BASE_URL")
    openai_model: str | None = Field(default=None, validation_alias="OPENAI_MODEL")
    anthropic_base_url: str | None = Field(default=None, validation_alias="ANTHROPIC_BASE_URL")
    anthropic_model: str | None = Field(default=None, validation_alias="ANTHROPIC_MODEL")
    # Additional browser origins; pages served by this process are always same-origin.
    allowed_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]

    @classmethod
    def without_env_file(cls, **values: Any) -> Self:
        """Settings from explicit values and environment variables, never from a .env file.

        Tests and smoke scripts use it so that a developer's .env cannot change their
        results. Type checkers do not know pydantic-settings' `_env_file` argument."""
        return cls(_env_file=None, **values)  # pyright: ignore[reportCallIssue]

    def initialize(self) -> None:
        """Resolve local paths and create the runtime data directory."""
        self.data_dir = self.data_dir.resolve()
        self.workspace_root = self.workspace_root.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def apply_provider(self, config: GlassboxConfig) -> GlassboxConfig:
        """Normalize saved settings on startup and apply explicit .env provider values.

        OPENAI_* and ANTHROPIC_* values update (or create) the `openai` and `anthropic`
        providers. GLASSBOX_PROVIDER, or the only provider configured in .env, becomes the
        default model."""
        values = config.model_copy(deep=True)
        # A data directory created on another OS names that OS's shell. Carry its permission
        # over to this OS's shell; an explicit permission for the native shell wins. Only
        # current settings change; stored Run snapshots keep their original tool names.
        foreign = "bash" if SHELL_TOOL == "powershell" else "powershell"
        if foreign in values.tools:
            permission = values.tools.pop(foreign)
            values.tools.setdefault(SHELL_TOOL, permission)
        explicit: dict[EnvProvider, str | None] = {}
        sources: tuple[tuple[EnvProvider, str | None, str | None], ...] = (
            ("openai", self.openai_base_url, self.openai_model),
            ("anthropic", self.anthropic_base_url, self.anthropic_model),
        )
        for adapter, base_url, model in sources:
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
        return GlassboxConfig.model_validate(values.model_dump())

    @property
    def database_url(self) -> str:
        """Build an absolute SQLite URL compatible with Windows paths."""
        return "sqlite+aiosqlite:///" + (self.data_dir.resolve() / "app.db").as_posix()
