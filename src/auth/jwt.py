"""JWT token creation and validation for OpsLens."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from jose import JWTError, jwt
from pydantic import BaseModel

JWT_SECRET: str = os.environ.get("JWT_SECRET", "opslens-dev-secret-change-in-production")
JWT_ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "7"))


class TokenData(BaseModel):
    """Decoded JWT payload."""

    user_id: uuid.UUID
    email: str
    role: str
    org_id: uuid.UUID
    exp: datetime
    token_type: str = "access"


def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed JWT access token.

    Args:
        data: Claims to embed. Must include ``sub`` (user_id), ``email``,
              ``role``, and ``org_id``.
        expires_delta: Custom expiry duration.  Defaults to
                       ``ACCESS_TOKEN_EXPIRE_MINUTES``.

    Returns:
        Encoded JWT string.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "token_type": "access",
    })
    # Ensure UUID values are serialised as strings
    for key in ("sub", "org_id"):
        if key in to_encode and isinstance(to_encode[key], uuid.UUID):
            to_encode[key] = str(to_encode[key])
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(data: dict[str, Any]) -> str:
    """Create a signed JWT refresh token (long-lived).

    Args:
        data: Claims to embed.  Same requirements as ``create_access_token``.

    Returns:
        Encoded JWT string.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "token_type": "refresh",
    })
    for key in ("sub", "org_id"):
        if key in to_encode and isinstance(to_encode[key], uuid.UUID):
            to_encode[key] = str(to_encode[key])
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> TokenData:
    """Decode and validate a JWT token.

    Args:
        token: Raw JWT string.

    Returns:
        Parsed ``TokenData``.

    Raises:
        JWTError: If the token is invalid, expired, or missing required claims.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise

    sub = payload.get("sub")
    email = payload.get("email")
    role = payload.get("role")
    org_id = payload.get("org_id")
    exp = payload.get("exp")
    token_type = payload.get("token_type", "access")

    if not all([sub, email, role, org_id, exp]):
        raise JWTError("Token is missing required claims")

    return TokenData(
        user_id=uuid.UUID(sub),
        email=email,
        role=role,
        org_id=uuid.UUID(org_id),
        exp=datetime.fromtimestamp(exp, tz=timezone.utc),
        token_type=token_type,
    )
