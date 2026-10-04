"""Process settings. Model credentials are resolved only at the provider boundary."""

import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from rtagent.domain import StudioConfig


def resolve_secret(reference: str) -> str:
    """Resolve an environment reference at execution time, with a local .env fallback."""
    if not reference:
        return ""
    if not reference.isidentifier() or not reference.isascii():
        raise ValueError("Credential references must be environment variable names.")
    return os.environ.get(reference) or dotenv_values(".env").get(reference) or ""


class Settings(BaseSettings):
    """Local process configuration kept outside versioned Run settings."""

    model_config = SettingsConfigDict(env_prefix="STUDIO_", env_file=".env", extra="ignore")

    data_dir: Path = Path("var")
    workspace_root: Path = Path(".")
    provider: Literal["openai", "fake", "anthropic"] | None = None
    openai_base_url: str | None = Field(default=None, validation_alias="OPENAI_BASE_URL")
    openai_model: str | None = Field(default=None, validation_alias="OPENAI_MODEL")
    anthropic_base_url: str | None = Field(default=None, validation_alias="ANTHROPIC_BASE_URL")
    anthropic_model: str | None = Field(default=None, validation_alias="ANTHROPIC_MODEL")
    allowed_origins: list[str] = [
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]

    def initialize(self) -> None:
        """Resolve local paths and create the runtime data directory."""
        self.data_dir = self.data_dir.resolve()
        self.workspace_root = self.workspace_root.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def apply_provider(self, config: StudioConfig) -> StudioConfig:
        """Apply explicit environment values on startup; later UI edits remain usable."""
        values = config.model_dump()
        # Preserve the previous shell permission when upgrading the Windows backend.
        # Only current settings change; stored Run snapshots keep their original tool names.
        if os.name == "nt" and "bash" in values["tools"]:
            permission = values["tools"].pop("bash")
            values["tools"].setdefault("powershell", permission)
        use_anthropic = self.provider == "anthropic" or (
            self.provider is None
            and (self.anthropic_base_url is not None or self.anthropic_model is not None)
            and self.openai_base_url is None
            and self.openai_model is None
        )
        overrides = {
            "adapter": self.provider,
            "base_url": self.anthropic_base_url if use_anthropic else self.openai_base_url,
            "model": self.anthropic_model if use_anthropic else self.openai_model,
        }
        if use_anthropic:
            overrides["adapter"] = "anthropic"
            if config.provider.adapter != "anthropic":
                overrides["base_url"] = self.anthropic_base_url or "https://api.anthropic.com/v1"
                overrides["model"] = self.anthropic_model or ""
                values["provider"]["credential_ref"] = "ANTHROPIC_API_KEY"
        elif config.provider.adapter == "anthropic" and (
            self.provider == "openai"
            or self.openai_base_url is not None
            or self.openai_model is not None
        ):
            overrides["base_url"] = self.openai_base_url or "https://api.openai.com/v1"
            overrides["model"] = self.openai_model or ""
            values["provider"]["credential_ref"] = "OPENAI_API_KEY"
        values["provider"].update(
            {key: value for key, value in overrides.items() if value is not None}
        )
        if self.provider is None and (self.openai_base_url or self.openai_model):
            values["provider"]["adapter"] = "openai"
        return StudioConfig.model_validate(values)

    @property
    def database_url(self) -> str:
        """Build an absolute SQLite URL compatible with Windows paths."""
        return "sqlite+aiosqlite:///" + (self.data_dir.resolve() / "app.db").as_posix()
