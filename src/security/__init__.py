"""OpsLens security package.

Provides encryption, rate limiting, circuit breakers, and CORS configuration
for production-ready API hardening.
"""

from src.security.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    github_circuit,
    llm_circuit,
    mcp_circuit,
    slack_circuit,
)
from src.security.cors import setup_cors
from src.security.encryption import (
    decrypt_settings,
    decrypt_value,
    encrypt_settings,
    encrypt_value,
    is_sensitive_key,
    load_encrypted_settings,
    save_encrypted_settings,
)
from src.security.rate_limiter import setup_rate_limiter

__all__ = [
    # Encryption
    "encrypt_value",
    "decrypt_value",
    "encrypt_settings",
    "decrypt_settings",
    "load_encrypted_settings",
    "save_encrypted_settings",
    "is_sensitive_key",
    # Rate limiting
    "setup_rate_limiter",
    # Circuit breaker
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "mcp_circuit",
    "llm_circuit",
    "slack_circuit",
    "github_circuit",
    # CORS
    "setup_cors",
]
