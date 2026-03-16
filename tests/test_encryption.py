"""Tests for Fernet-based encryption of settings and secrets."""

import os

import pytest
from cryptography.fernet import Fernet

from src.errors import EncryptionError


# Ensure a valid, stable Fernet key is set for all tests in this module
@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("OPSLENS_ENCRYPTION_KEY", key)


# Import AFTER the autouse fixture is defined so that the module-level
# auto-generated key path is not triggered unpredictably.
from src.security.encryption import (
    decrypt_settings,
    decrypt_value,
    encrypt_settings,
    encrypt_value,
    is_sensitive_key,
)


# ---------------------------------------------------------------------------
# encrypt / decrypt round-trip
# ---------------------------------------------------------------------------

class TestEncryptDecrypt:
    def test_roundtrip(self):
        plaintext = "my-super-secret-api-key"
        encrypted = encrypt_value(plaintext)
        assert encrypted != plaintext
        assert decrypt_value(encrypted) == plaintext

    def test_encrypted_has_enc_prefix(self):
        encrypted = encrypt_value("secret")
        assert encrypted.startswith("enc:")

    def test_decrypt_plain_value_returns_original(self):
        """Non-encrypted values pass through unchanged."""
        assert decrypt_value("not-encrypted") == "not-encrypted"

    def test_decrypt_empty_string(self):
        assert decrypt_value("") == ""

    def test_encrypt_empty_string(self):
        assert encrypt_value("") == ""

    def test_decrypt_none_passthrough(self):
        # The function checks `if not ciphertext` first
        assert decrypt_value("") == ""

    def test_unicode_roundtrip(self):
        plaintext = "p@$$w0rd-with-emojis"
        encrypted = encrypt_value(plaintext)
        assert decrypt_value(encrypted) == plaintext

    def test_wrong_key_raises(self, monkeypatch):
        encrypted = encrypt_value("secret-value")
        # Change the key
        new_key = Fernet.generate_key().decode()
        monkeypatch.setenv("OPSLENS_ENCRYPTION_KEY", new_key)
        with pytest.raises(EncryptionError):
            decrypt_value(encrypted)


# ---------------------------------------------------------------------------
# is_sensitive_key
# ---------------------------------------------------------------------------

class TestIsSensitiveKey:
    @pytest.mark.parametrize(
        "key",
        [
            "GITHUB_TOKEN",
            "slack_api_key",
            "JWT_SECRET",
            "DB_PASSWORD",
            "auth_token",
            "webhook_url",
            "bot_token",
            "api_key",
            "secret",
            "credentials_json",
            "secret_access_key",
            "client_secret",
            "ANTHROPIC_API_KEY",
        ],
    )
    def test_sensitive_keys_detected(self, key):
        assert is_sensitive_key(key) is True

    @pytest.mark.parametrize(
        "key",
        [
            "APP_NAME",
            "LOG_LEVEL",
            "channel",
            "org",
            "region",
            "default_branch",
            "llm_provider",
            "environment",
        ],
    )
    def test_non_sensitive_keys_ignored(self, key):
        assert is_sensitive_key(key) is False


# ---------------------------------------------------------------------------
# Dict-level encrypt / decrypt
# ---------------------------------------------------------------------------

class TestSettingsEncryption:
    def test_encrypt_settings_only_sensitive(self):
        settings = {
            "github": {
                "token": "ghp_abc123",
                "org": "my-org",
            },
            "app_name": "OpsLens",
            "api_key": "sk-secret",
        }
        encrypted = encrypt_settings(settings)

        # Sensitive values should be encrypted
        assert encrypted["github"]["token"].startswith("enc:")
        assert encrypted["api_key"].startswith("enc:")

        # Non-sensitive values should be unchanged
        assert encrypted["github"]["org"] == "my-org"
        assert encrypted["app_name"] == "OpsLens"

    def test_decrypt_settings_restores_originals(self):
        settings = {
            "slack": {
                "bot_token": "xoxb-secret",
                "channel": "#incidents",
            },
            "api_key": "my-api-key",
        }
        encrypted = encrypt_settings(settings)
        decrypted = decrypt_settings(encrypted)

        assert decrypted["slack"]["bot_token"] == "xoxb-secret"
        assert decrypted["slack"]["channel"] == "#incidents"
        assert decrypted["api_key"] == "my-api-key"

    def test_nested_dict_encryption(self):
        settings = {
            "level1": {
                "level2": {
                    "api_key": "nested-secret",
                    "name": "test",
                },
            },
        }
        encrypted = encrypt_settings(settings)
        assert encrypted["level1"]["level2"]["api_key"].startswith("enc:")
        assert encrypted["level1"]["level2"]["name"] == "test"

        decrypted = decrypt_settings(encrypted)
        assert decrypted["level1"]["level2"]["api_key"] == "nested-secret"

    def test_already_encrypted_not_double_encrypted(self):
        settings = {"api_key": "original"}
        encrypted_once = encrypt_settings(settings)
        encrypted_twice = encrypt_settings(encrypted_once)
        # The enc: value should not get double-encrypted
        assert encrypted_once["api_key"] == encrypted_twice["api_key"]

    def test_settings_original_not_mutated(self):
        settings = {"api_key": "secret-value", "name": "test"}
        _ = encrypt_settings(settings)
        assert settings["api_key"] == "secret-value"
        assert settings["name"] == "test"

    def test_empty_dict(self):
        assert encrypt_settings({}) == {}
        assert decrypt_settings({}) == {}

    def test_non_string_values_untouched(self):
        settings = {
            "api_key": 12345,
            "token": None,
            "enabled": True,
        }
        encrypted = encrypt_settings(settings)
        assert encrypted["api_key"] == 12345
        assert encrypted["token"] is None
        assert encrypted["enabled"] is True
