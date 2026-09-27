import os
import re
import pytest
from pathlib import Path

from config.settings import Settings, get_settings
from infrastructure.secrets.client import (
    EnvSecretsClient,
    VaultSecretsClient,
    get_secrets_client,
)
from config.errors import NotFoundError, ExternalServiceError


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://custom_user:pass@db:5432/custom_db")
    monkeypatch.setenv("RISK_THRESHOLD_MEDIUM", "4")
    monkeypatch.setenv("RISK_THRESHOLD_HIGH", "6")
    monkeypatch.setenv("SECRETS_BACKEND", "vault")

    settings = Settings()
    assert settings.database_url == "postgresql://custom_user:pass@db:5432/custom_db"
    assert settings.risk_threshold_medium == 4
    assert settings.risk_threshold_high == 6
    assert settings.secrets_backend == "vault"


def test_env_client_reads_var(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "secret-key-1234")
    client = EnvSecretsClient()
    assert client.get_secret("TEST_API_KEY") == "secret-key-1234"

    with pytest.raises(NotFoundError):
        client.get_secret("NON_EXISTENT_KEY")


def test_vault_client_mocked_fetch(monkeypatch):
    client = VaultSecretsClient(vault_addr="http://vault.internal:8200")
    client.set_mock_secret("DB_PASSWORD", "vault-db-pass-5678")
    assert client.get_secret("DB_PASSWORD") == "vault-db-pass-5678"

    # Test environment-fallback lookup for Vault client
    monkeypatch.setenv("VAULT_SECRET_LLM_KEY", "vault-llm-val")
    assert client.get_secret("LLM_KEY") == "vault-llm-val"

    with pytest.raises(ExternalServiceError):
        client.get_secret("MISSING_VAULT_KEY")


def test_secrets_never_appear_in_settings_repr():
    settings = Settings()
    repr_str = repr(settings)
    # Ensure sensitive client secrets or passwords are not in default string representation
    # Sensitive credentials should come from secrets client, not logged in settings
    assert "vault-db-pass" not in repr_str
    assert "secret-key-1234" not in repr_str


def test_no_direct_os_environ_reads_outside_settings_py():
    """Lint-style test: verify services/ and domain/ do not read os.environ directly."""
    root_dir = Path(__file__).parent.parent.parent
    prohibited_dirs = [root_dir / "services", root_dir / "domain"]
    
    violations = []
    pattern = re.compile(r"os\.environ(?:\[|\.get\()")

    for pdir in prohibited_dirs:
        if not pdir.exists():
            continue
        for py_file in pdir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            if pattern.search(content):
                violations.append(str(py_file.relative_to(root_dir)))

    assert violations == [], f"Direct os.environ reads found in: {violations}. Use config.settings.get_settings() instead."
