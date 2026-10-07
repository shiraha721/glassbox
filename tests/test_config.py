"""Ensure serialized connection settings contain references rather than embedded secrets."""

import os

import pytest
from pydantic import ValidationError

from rtagent.domain import (
    SIMULATED,
    AppError,
    MCPServer,
    ModelChoice,
    ProviderConfig,
    ProviderProfile,
    StudioConfig,
    resolve_model,
)
from rtagent.domain.models import default_providers
from rtagent.infrastructure import config as config_module
from rtagent.infrastructure.config import Settings
from rtagent.infrastructure.credentials import CredentialStore
from rtagent.infrastructure.storage import with_provider_profiles


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


@pytest.mark.parametrize(("native", "foreign"), [("powershell", "bash"), ("bash", "powershell")])
def test_shell_permission_follows_the_data_directory_across_systems(monkeypatch, native, foreign):
    """Moving data between Windows and Linux keeps the shell permission without granting more."""
    monkeypatch.setattr(config_module, "SHELL_TOOL", native)
    settings = Settings(_env_file=None)
    for permission in ("allow", "ask", "deny"):
        original = StudioConfig(tools={foreign: permission})
        configured = settings.apply_provider(original)
        assert configured.tools == {native: permission}
        # Existing Runs keep the snapshot they were given.
        assert original.tools == {foreign: permission}
    explicit = StudioConfig(tools={foreign: "allow", native: "deny"})
    assert settings.apply_provider(explicit).tools == {native: "deny"}


def test_model_choices_resolve_against_configured_providers():
    """A Run's choice takes connection and options from its provider; unknown values fail."""
    config = StudioConfig(
        providers=[
            ProviderProfile(
                id="claude",
                name="Claude",
                adapter="anthropic",
                base_url="https://api.anthropic.com/v1",
                credential_ref="ANTHROPIC_API_KEY",
                max_output_tokens=32000,
            )
        ]
    )
    chosen = resolve_model(
        config, ModelChoice(provider_id="claude", model="claude-opus-5-5", reasoning_effort="max")
    )
    assert (chosen.adapter, chosen.model, chosen.reasoning_effort) == (
        "anthropic",
        "claude-opus-5-5",
        "max",
    )
    assert chosen.max_output_tokens == 32000 and chosen.temperature is None
    simulated = resolve_model(config, ModelChoice(provider_id=SIMULATED, model="simulated"))
    assert simulated.adapter == "fake"
    with pytest.raises(AppError, match="not configured"):
        resolve_model(config, ModelChoice(provider_id="missing", model="x"))
    openai = config.model_copy(update={"providers": default_providers()})
    with pytest.raises(AppError, match="reasoning effort"):
        resolve_model(openai, ModelChoice(provider_id="openai", model="m", reasoning_effort="max"))


def test_env_values_update_provider_profiles_and_the_default_model():
    """OPENAI_* and ANTHROPIC_* values configure providers; STUDIO_PROVIDER picks the default."""
    settings = Settings(
        _env_file=None,
        provider="anthropic",
        openai_base_url="https://api.deepseek.com/v1",
        openai_model="deepseek-chat",
        anthropic_model="claude-opus-5-5",
    )
    configured = settings.apply_provider(StudioConfig())
    profiles = {profile.id: profile for profile in configured.providers}
    assert profiles["openai"].base_url == "https://api.deepseek.com/v1"
    assert profiles["openai"].models == ["deepseek-chat"]
    assert profiles["anthropic"].credential_ref == "ANTHROPIC_API_KEY"
    assert (configured.provider.profile_id, configured.provider.model) == (
        "anthropic",
        "claude-opus-5-5",
    )
    simulated = Settings(_env_file=None, provider="fake").apply_provider(configured)
    assert simulated.provider.adapter == "fake" and simulated.providers == configured.providers


def test_legacy_single_provider_settings_become_a_profile():
    """Settings saved before provider profiles keep their endpoint, key name and options."""
    legacy = StudioConfig().model_dump()
    del legacy["providers"]
    legacy["provider"] = {
        "adapter": "openai",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "credential_ref": "OPENAI_API_KEY",
        "temperature": 0.2,
        "max_output_tokens": 8192,
        "context_window": None,
    }
    upgraded = StudioConfig.model_validate(with_provider_profiles(legacy))
    assert upgraded.providers[0].base_url == "https://api.deepseek.com/v1"
    assert upgraded.providers[0].temperature == 0.2
    assert resolve_model(upgraded).model == "deepseek-chat"


def test_saved_keys_take_precedence_and_are_never_reported(tmp_path, monkeypatch):
    """A key saved in Settings wins over the environment; status reveals only its source."""
    monkeypatch.setenv("STUDIO_TEST_KEY", "from-environment")
    store = CredentialStore(tmp_path / "credentials.json")
    assert store.status("STUDIO_TEST_KEY").source == "environment"
    store.save("STUDIO_TEST_KEY", "sk-saved-0123456789")
    assert store.resolve("STUDIO_TEST_KEY") == "sk-saved-0123456789"
    status = store.status("STUDIO_TEST_KEY")
    assert (status.source, status.hint) == ("stored", "6789")
    if os.name != "nt":
        assert (tmp_path / "credentials.json").stat().st_mode & 0o777 == 0o600
    store.delete("STUDIO_TEST_KEY")
    assert store.resolve("STUDIO_TEST_KEY") == "from-environment"
    assert CredentialStore(None).status("STUDIO_MISSING_KEY").source == "missing"
