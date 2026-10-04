"""Ensure serialized connection settings contain references rather than embedded secrets."""

import os

import pytest
from pydantic import ValidationError

from rtagent.domain import MCPServer, ProviderConfig, StudioConfig
from rtagent.infrastructure.config import Settings


@pytest.mark.parametrize(
    "factory", [ProviderConfig, lambda **values: MCPServer(name="fixture", **values)]
)
def test_literal_credential_values_are_rejected(factory):
    """Reject common literal key syntax before it can enter a stored snapshot."""
    with pytest.raises(ValidationError):
        factory(credential_ref="sk-example123456789")


def test_mcp_env_and_urls_do_not_embed_credentials():
    """Child environment values are variable references and HTTP authentication is separate."""
    with pytest.raises(ValidationError):
        MCPServer(name="fixture", env={"TOKEN": "literal-secret-value"})
    with pytest.raises(ValidationError):
        MCPServer(name="fixture", url="https://user:password@example.test/mcp")
    with pytest.raises(ValidationError):
        ProviderConfig(base_url="https://example.test/v1?api_key=secret")


@pytest.mark.skipif(os.name != "nt", reason="Migration applies only to Windows settings.")
def test_windows_shell_upgrade_preserves_permissions_and_old_snapshots():
    """Replacing the old shell must not grant permission or rewrite an existing Run's config."""
    settings = Settings(_env_file=None)
    for permission in ("allow", "ask", "deny"):
        original = StudioConfig(tools={"bash": permission})
        configured = settings.apply_provider(original)
        assert configured.tools == {"powershell": permission}
        assert original.tools == {"bash": permission}
    explicit = StudioConfig(tools={"bash": "allow", "powershell": "deny"})
    assert settings.apply_provider(explicit).tools == {"powershell": "deny"}
