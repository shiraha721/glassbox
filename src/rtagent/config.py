"""Process settings. Model credentials are resolved only at the provider boundary."""

import os
import secrets
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict


def resolve_secret(reference: str) -> str:
    if not reference:
        return ""
    if not reference.isidentifier() or not reference.isascii():
        raise ValueError("Credential references must be environment variable names.")
    return os.environ.get(reference) or dotenv_values(".env").get(reference) or ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STUDIO_", env_file=".env", extra="ignore")

    data_dir: Path = Path("var")
    workspace_root: Path = Path(".")
    access_token: str = ""
    allowed_origins: list[str] = [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]

    def initialize(self) -> None:
        self.data_dir = self.data_dir.resolve()
        self.workspace_root = self.workspace_root.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.access_token:
            token_path = self.data_dir / "access-token"
            if not token_path.exists():
                try:
                    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        handle.write(secrets.token_urlsafe(32))
                except FileExistsError:
                    pass
            self.access_token = token_path.read_text(encoding="utf-8").strip()

    @property
    def database_url(self) -> str:
        return "sqlite+aiosqlite:///" + (self.data_dir.resolve() / "app.db").as_posix()
