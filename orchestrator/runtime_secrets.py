"""Runtime secret bootstrap from Azure Key Vault.

This module keeps secrets out of .env files by resolving them at process start
and writing values into ``os.environ`` for existing consumers.
"""

from __future__ import annotations

import os
from typing import Final

_REQUIRED_SECRETS: Final[dict[str, str]] = {
    "JIRA_URL": "JIRA-URL",
    "JIRA_EMAIL": "JIRA-EMAIL",
    "JIRA_API_TOKEN": "JIRA-API-TOKEN",
    "AZURE_API_KEY": "AZURE-API-KEY",
    "ANTHROPIC_API_KEY": "ANTHROPIC-API-KEY",
    "GITHUB_APP_ID": "GITHUB-APP-ID",
    "GITHUB_APP_PRIVATE_KEY": "GITHUB-APP-PRIVATE-KEY",
    "GITHUB_APP_INSTALLATION_ID": "GITHUB-APP-INSTALLATION-ID",
}


def load_runtime_secrets_from_keyvault() -> None:
    """Load required runtime secrets from Azure Key Vault.

    Behavior:
    - No-op if ``AZURE_KEYVAULT_NAME`` is unset.
    - Does not overwrite variables already present in the environment.
    - Raises ``RuntimeError`` when a configured vault cannot satisfy required
      secret retrieval.
    """

    vault_name = (os.getenv("AZURE_KEYVAULT_NAME") or "").strip()
    if not vault_name:
        return

    missing_env_vars = [name for name in _REQUIRED_SECRETS if not os.getenv(name)]
    if not missing_env_vars:
        return

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except Exception as exc:  # pragma: no cover - defensive import guard
        raise RuntimeError(
            "Azure Key Vault is configured (AZURE_KEYVAULT_NAME) but Azure SDK "
            "dependencies are missing. Install requirements.txt and retry."
        ) from exc

    vault_url = f"https://{vault_name}.vault.azure.net"
    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())

    failures: list[str] = []
    for env_name in missing_env_vars:
        secret_name = _REQUIRED_SECRETS[env_name]
        try:
            value = client.get_secret(secret_name).value
        except Exception as exc:  # pragma: no cover - SDK exception surface varies
            failures.append(f"{env_name} (secret: {secret_name}): {exc}")
            continue

        if value is None or value == "":
            failures.append(f"{env_name} (secret: {secret_name}): empty secret value")
            continue

        os.environ[env_name] = value

    if failures:
        details = "\n- ".join(failures)
        raise RuntimeError(
            "Failed loading required secrets from Azure Key Vault " f"'{vault_name}'.\n- {details}"
        )
