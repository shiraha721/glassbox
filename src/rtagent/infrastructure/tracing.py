"""Redacted views never overwrite original execution evidence."""

import re
from typing import Any


def redact(value: Any, secrets: list[str] | None = None) -> Any:
    """Return a masked copy of common secret patterns without modifying stored evidence."""
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if key.lower() in {"api_key", "password", "authorization", "access_token"}
                else redact(item, secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets or []:
            if len(secret) >= 4:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", value)
        value = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+", r"\1[REDACTED]", value)
    return value
