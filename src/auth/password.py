"""Password hashing and verification using passlib + bcrypt."""

from __future__ import annotations

from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plain-text password with bcrypt.

    Args:
        password: The plain-text password.

    Returns:
        Bcrypt hash string.
    """
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against a bcrypt hash.

    Args:
        plain: Plain-text password to check.
        hashed: Previously hashed password.

    Returns:
        ``True`` if the password matches.
    """
    return _pwd_context.verify(plain, hashed)
