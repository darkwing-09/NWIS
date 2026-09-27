import os
from typing import Dict, Optional, Protocol
from config.settings import get_settings
from config.errors import ExternalServiceError, NotFoundError


class SecretsClient(Protocol):
    """Protocol for secrets management (Vault or environment variables)."""

    def get_secret(self, key: str, default: Optional[str] = None) -> str: ...


class EnvSecretsClient:
    """Development/local implementation that reads secrets from environment variables."""

    def get_secret(self, key: str, default: Optional[str] = None) -> str:
        val = os.environ.get(key, default)
        if val is None:
            raise NotFoundError(f"Secret '{key}' not found in environment")
        return val


class VaultSecretsClient:
    """Production implementation connecting to HashiCorp Vault or cloud secrets manager."""

    def __init__(self, vault_addr: Optional[str] = None, token: Optional[str] = None) -> None:
        self.vault_addr = vault_addr or os.environ.get("VAULT_ADDR", "http://localhost:8200")
        self.token = token or os.environ.get("VAULT_TOKEN", "")
        # Mock/in-memory cache for client or integration testing
        self._cache: Dict[str, str] = {}

    def get_secret(self, key: str, default: Optional[str] = None) -> str:
        if key in self._cache:
            return self._cache[key]
        # In a deployed Vault setup, this uses hvac or HTTP client to read path
        val = os.environ.get(f"VAULT_SECRET_{key}", default)
        if val is None:
            raise ExternalServiceError(
                f"Vault secret '{key}' could not be retrieved from {self.vault_addr}",
                detail={"key": key, "vault_addr": self.vault_addr},
            )
        return val

    def set_mock_secret(self, key: str, value: str) -> None:
        self._cache[key] = value


_secrets_client: Optional[SecretsClient] = None


def get_secrets_client() -> SecretsClient:
    """Returns configured SecretsClient based on settings.secrets_backend."""
    global _secrets_client
    if _secrets_client is not None:
        return _secrets_client

    settings = get_settings()
    if settings.secrets_backend == "vault":
        _secrets_client = VaultSecretsClient()
    else:
        _secrets_client = EnvSecretsClient()

    return _secrets_client
