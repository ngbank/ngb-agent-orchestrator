import os
import sys
import types
from typing import Any, cast

import pytest

from orchestrator.runtime_secrets import load_runtime_secrets_from_keyvault


def _install_fake_azure(
    monkeypatch, values: dict[str, str], fail_secret: str | None = None
) -> None:
    azure_module = types.ModuleType("azure")
    identity_module = cast(Any, types.ModuleType("azure.identity"))
    keyvault_module = types.ModuleType("azure.keyvault")
    secrets_module = cast(Any, types.ModuleType("azure.keyvault.secrets"))

    class FakeDefaultAzureCredential:
        pass

    class FakeSecretClient:
        def __init__(self, vault_url: str, credential: object):
            self.vault_url = vault_url
            self.credential = credential

        def get_secret(self, name: str):
            if fail_secret and name == fail_secret:
                raise RuntimeError("secret not found")
            return types.SimpleNamespace(value=values[name])

    identity_module.DefaultAzureCredential = FakeDefaultAzureCredential
    secrets_module.SecretClient = FakeSecretClient

    monkeypatch.setitem(sys.modules, "azure", azure_module)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_module)
    monkeypatch.setitem(sys.modules, "azure.keyvault", keyvault_module)
    monkeypatch.setitem(sys.modules, "azure.keyvault.secrets", secrets_module)


def test_noop_when_vault_name_not_set(monkeypatch):
    monkeypatch.delenv("AZURE_KEYVAULT_NAME", raising=False)
    load_runtime_secrets_from_keyvault()


def test_noop_when_required_env_already_present(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_NAME", "kv-test")
    monkeypatch.setenv("JIRA_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setenv("AZURE_API_KEY", "key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "pem")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "456")

    load_runtime_secrets_from_keyvault()


def test_loads_missing_values_from_keyvault(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_NAME", "kv-test")
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    monkeypatch.delenv("AZURE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)

    _install_fake_azure(
        monkeypatch,
        {
            "JIRA-URL": "https://example.atlassian.net",
            "JIRA-EMAIL": "user@example.com",
            "JIRA-API-TOKEN": "jira-token",
            "AZURE-API-KEY": "azure-key",
            "ANTHROPIC-API-KEY": "anthropic-key",
            "GITHUB-APP-ID": "123",
            "GITHUB-APP-PRIVATE-KEY": "pem",
            "GITHUB-APP-INSTALLATION-ID": "456",
        },
    )

    load_runtime_secrets_from_keyvault()

    assert "https://example.atlassian.net" == os.environ["JIRA_URL"]
    assert "user@example.com" == os.environ["JIRA_EMAIL"]
    assert "jira-token" == os.environ["JIRA_API_TOKEN"]
    assert "azure-key" == os.environ["AZURE_API_KEY"]
    assert "anthropic-key" == os.environ["ANTHROPIC_API_KEY"]
    assert "123" == os.environ["GITHUB_APP_ID"]
    assert "pem" == os.environ["GITHUB_APP_PRIVATE_KEY"]
    assert "456" == os.environ["GITHUB_APP_INSTALLATION_ID"]


def test_raises_when_secret_fetch_fails(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_NAME", "kv-test")
    monkeypatch.delenv("JIRA_URL", raising=False)

    _install_fake_azure(
        monkeypatch,
        {
            "JIRA-URL": "https://example.atlassian.net",
            "JIRA-EMAIL": "user@example.com",
            "JIRA-API-TOKEN": "jira-token",
            "AZURE-API-KEY": "azure-key",
            "ANTHROPIC-API-KEY": "anthropic-key",
            "GITHUB-APP-ID": "123",
            "GITHUB-APP-PRIVATE-KEY": "pem",
            "GITHUB-APP-INSTALLATION-ID": "456",
        },
        fail_secret="JIRA-URL",
    )

    with pytest.raises(RuntimeError) as exc:
        load_runtime_secrets_from_keyvault()

    assert "Failed loading required secrets" in str(exc.value)
    assert "JIRA_URL" in str(exc.value)
