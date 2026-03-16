"""Fernet-based encryption for OpsLens settings and secrets.

Sensitive values (API keys, tokens, passwords) are encrypted at rest in
``settings.json`` using symmetric Fernet encryption.  The encryption key is
read from the ``OPSLENS_ENCRYPTION_KEY`` environment variable.  If the
variable is absent a new key is generated automatically and a warning is
logged (suitable for development; in production the key **must** be set
explicitly and persisted).
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

import structlog
from cryptography.fernet import Fernet, InvalidToken

from src.errors import EncryptionError

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

_SENSITIVE_KEY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r".*_KEY$", re.IGNORECASE),
    re.compile(r".*_TOKEN$", re.IGNORECASE),
    re.compile(r".*_SECRET$", re.IGNORECASE),
    re.compile(r".*_PASSWORD$", re.IGNORECASE),
]

# Exact field names that are always sensitive regardless of suffix
_SENSITIVE_EXACT_NAMES: set[str] = {
    "auth_token",
    "webhook_url",
    "bot_token",
    "token",
    "api_token",
    "api_key",
    "secret",
    "credentials_json",
    "secret_access_key",
    "access_key_id",
    "client_secret",
}


def _get_encryption_key() -> bytes:
    """Return the Fernet key, generating one if not configured.

    Returns:
        Raw Fernet key bytes (URL-safe base64-encoded 32-byte key).
    """
    env_key = os.environ.get("OPSLENS_ENCRYPTION_KEY", "")
    if env_key:
        try:
            # Validate it is a proper Fernet key
            Fernet(env_key.encode())
            return env_key.encode()
        except (ValueError, Exception) as exc:
            logger.error("invalid_encryption_key", error=str(exc))
            raise EncryptionError(
                "OPSLENS_ENCRYPTION_KEY is set but not a valid Fernet key. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            ) from exc

    # Auto-generate for development convenience
    generated = Fernet.generate_key()
    logger.warning(
        "encryption_key_auto_generated",
        hint="Set OPSLENS_ENCRYPTION_KEY env var for production. "
        "Auto-generated keys change on restart, making previously encrypted values unreadable.",
    )
    # Stash in the environment so the same key is used for the lifetime of
    # this process (avoids double-generate across import).
    os.environ["OPSLENS_ENCRYPTION_KEY"] = generated.decode()
    return generated


def _get_fernet() -> Fernet:
    """Return a configured ``Fernet`` instance."""
    return Fernet(_get_encryption_key())


# ---------------------------------------------------------------------------
# Low-level encrypt / decrypt
# ---------------------------------------------------------------------------


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string and return URL-safe base64-encoded ciphertext.

    Args:
        plaintext: The secret value to encrypt.

    Returns:
        Base64-encoded ciphertext string prefixed with ``enc:`` so that
        encrypted values are easily distinguishable from plain values.

    Raises:
        EncryptionError: If encryption fails.
    """
    if not plaintext:
        return plaintext
    try:
        fernet = _get_fernet()
        token = fernet.encrypt(plaintext.encode("utf-8"))
        return "enc:" + token.decode("utf-8")
    except Exception as exc:
        logger.error("encrypt_value_failed", error=str(exc))
        raise EncryptionError(f"Failed to encrypt value: {exc}") from exc


def decrypt_value(ciphertext: str) -> str:
    """Decrypt an ``enc:``-prefixed ciphertext string back to plaintext.

    If the value does not carry the ``enc:`` prefix it is returned as-is
    (supports transparent migration from unencrypted settings).

    Args:
        ciphertext: The encrypted value (with ``enc:`` prefix).

    Returns:
        Decrypted plaintext string.

    Raises:
        EncryptionError: If decryption fails (wrong key, corrupted data).
    """
    if not ciphertext or not ciphertext.startswith("enc:"):
        return ciphertext
    try:
        fernet = _get_fernet()
        raw_token = ciphertext[4:]  # strip "enc:" prefix
        return fernet.decrypt(raw_token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        logger.error("decrypt_value_failed", error="Invalid token - encryption key may have changed")
        raise EncryptionError(
            "Failed to decrypt value. The encryption key may have changed since "
            "this value was encrypted. Ensure OPSLENS_ENCRYPTION_KEY is consistent."
        ) from exc
    except Exception as exc:
        logger.error("decrypt_value_failed", error=str(exc))
        raise EncryptionError(f"Failed to decrypt value: {exc}") from exc


# ---------------------------------------------------------------------------
# Key detection
# ---------------------------------------------------------------------------


def is_sensitive_key(key: str) -> bool:
    """Determine whether a settings key name represents a sensitive value.

    Matches keys ending with ``_KEY``, ``_TOKEN``, ``_SECRET``, or
    ``_PASSWORD`` (case-insensitive), as well as known exact field names
    like ``auth_token``, ``webhook_url``, etc.

    Args:
        key: The settings field name to check.

    Returns:
        True if the key should be treated as sensitive.
    """
    if key.lower() in _SENSITIVE_EXACT_NAMES:
        return True
    return any(pattern.match(key) for pattern in _SENSITIVE_KEY_PATTERNS)


# ---------------------------------------------------------------------------
# Dict-level encrypt / decrypt
# ---------------------------------------------------------------------------


def encrypt_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Recursively encrypt sensitive values in a settings dict.

    Only string values whose key matches :func:`is_sensitive_key` are
    encrypted.  Already-encrypted values (``enc:`` prefix) are left
    untouched to avoid double-encryption.

    Args:
        settings: The settings dict (will **not** be mutated).

    Returns:
        A new dict with sensitive string values encrypted.
    """
    return _walk_settings(settings, _encrypt_if_sensitive)


def decrypt_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Recursively decrypt sensitive values in a settings dict.

    Values without the ``enc:`` prefix are passed through unchanged.

    Args:
        settings: The settings dict (will **not** be mutated).

    Returns:
        A new dict with all ``enc:``-prefixed values decrypted.
    """
    return _walk_settings(settings, _decrypt_if_encrypted)


def _encrypt_if_sensitive(key: str, value: Any) -> Any:
    """Encrypt a value if its key is sensitive and it is not already encrypted."""
    if isinstance(value, str) and value and is_sensitive_key(key) and not value.startswith("enc:"):
        return encrypt_value(value)
    return value


def _decrypt_if_encrypted(key: str, value: Any) -> Any:
    """Decrypt a value if it carries the ``enc:`` prefix."""
    if isinstance(value, str) and value.startswith("enc:"):
        return decrypt_value(value)
    return value


def _walk_settings(
    data: dict[str, Any],
    transform: callable,
) -> dict[str, Any]:
    """Recursively walk a settings dict, applying *transform* to leaf values.

    Args:
        data: The input dict.
        transform: A callable ``(key, value) -> new_value``.

    Returns:
        A new dict with transformed values.
    """
    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result[key] = _walk_settings(value, transform)
        else:
            result[key] = transform(key, value)
    return result


# ---------------------------------------------------------------------------
# File-level operations
# ---------------------------------------------------------------------------


def load_encrypted_settings(path: str) -> dict[str, Any]:
    """Load ``settings.json`` from *path*, decrypting sensitive fields.

    If the file does not exist an empty dict is returned.

    Args:
        path: Filesystem path to the settings JSON file.

    Returns:
        Settings dict with all sensitive values in plaintext.

    Raises:
        EncryptionError: If decryption of any value fails.
    """
    settings_path = Path(path)
    if not settings_path.exists():
        logger.info("encrypted_settings_not_found", path=path)
        return {}

    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("load_encrypted_settings_failed", path=path, error=str(exc))
        raise EncryptionError(f"Failed to load settings from {path}: {exc}") from exc

    decrypted = decrypt_settings(raw)
    logger.debug("encrypted_settings_loaded", path=path)
    return decrypted


def save_encrypted_settings(path: str, settings: dict[str, Any]) -> None:
    """Encrypt sensitive fields and write to *path* as JSON.

    Args:
        path: Filesystem path for the output JSON file.
        settings: Settings dict with plaintext values.

    Raises:
        EncryptionError: If encryption of any value fails.
    """
    encrypted = encrypt_settings(settings)

    settings_path = Path(path)
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(encrypted, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.error("save_encrypted_settings_failed", path=path, error=str(exc))
        raise EncryptionError(f"Failed to save settings to {path}: {exc}") from exc

    logger.info("encrypted_settings_saved", path=path)
