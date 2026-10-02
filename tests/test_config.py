"""Ensure serialized connection settings contain references rather than embedded secrets."""

import pytest
from pydantic import ValidationError

from rtagent.domain import MCPServer, ProviderConfig


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
