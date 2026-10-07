"""API keys entered in Settings, kept on the server and never returned to clients."""

import json
import os
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile

from rtagent.domain import CredentialStatus, credential_reference
from rtagent.infrastructure.config import resolve_secret


class CredentialStore:
    """Resolve named credentials: a key saved in Settings first, then the environment.

    Saved keys live in one JSON file in the data directory. On POSIX the file is readable only
    by the owner; on Windows it inherits the data directory's ACL, like `.env` and the database.
    The data directory is hidden from file tools and commands, and values never leave the
    server: status reports only the source and the last four characters."""

    def __init__(self, path: Path | None) -> None:
        """Use the given file, or only the environment when no path is configured."""
        self.path = path
        self.lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        if self.path is None or not self.path.exists():
            return {}
        values = json.loads(self.path.read_text(encoding="utf-8"))
        return {key: value for key, value in values.items() if isinstance(value, str)}

    def _write(self, values: dict[str, str]) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, delete=False, prefix=".credentials-"
        ) as handle:
            temporary = Path(handle.name)
            # Restrict the file before it receives any secret (a no-op for ACLs on Windows).
            os.chmod(temporary, 0o600)
            json.dump(values, handle)
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def resolve(self, name: str) -> str:
        """Return the key for a reference name, or an empty string when none is configured."""
        if not name:
            return ""
        credential_reference(name)
        with self.lock:
            stored = self._load().get(name)
        return stored or resolve_secret(name)

    def save(self, name: str, value: str) -> None:
        """Store or replace a key under its reference name."""
        credential_reference(name)
        if self.path is None:
            raise ValueError("This service has no data directory for saved keys.")
        with self.lock:
            values = self._load()
            values[name] = value
            self._write(values)

    def delete(self, name: str) -> None:
        """Forget a saved key; an environment variable of the same name still applies."""
        with self.lock:
            values = self._load()
            if values.pop(name, None) is not None:
                self._write(values)

    def status(self, name: str) -> CredentialStatus:
        """Describe where a key would come from without revealing it."""
        credential_reference(name)
        with self.lock:
            stored = self._load().get(name)
        if stored:
            return CredentialStatus(name=name, source="stored", hint=stored[-4:])
        if resolve_secret(name):
            return CredentialStatus(name=name, source="environment")
        return CredentialStatus(name=name, source="missing")
