"""Process settings. Model credentials are resolved only at the provider boundary."""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def database_url(self) -> str:
        """Build an absolute SQLite URL compatible with Windows paths."""
        return "sqlite+aiosqlite:///" + (self.data_dir.resolve() / "app.db").as_posix()
