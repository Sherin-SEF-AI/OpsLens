"""FastAPI authentication dependencies for OpsLens."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.jwt import decode_token
from src.auth.rbac import ROLE_HIERARCHY
from src.database.engine import get_db
from src.database.models import User, UserRole
from src.database.repositories.users import UserRepository

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Validate the JWT bearer token and return the corresponding ``User``.

    Args:
        token: Bearer token extracted by ``oauth2_scheme``.
        db: Async database session.

    Returns:
        The authenticated ``User`` model instance.

    Raises:
        HTTPException(401): If the token is invalid, expired, or the user
            does not exist.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token_data = decode_token(token)
    except JWTError:
        raise credentials_exception

    if token_data.token_type != "access":
        raise credentials_exception

    repo = UserRepository(db)
    user = await repo.get_by_id(token_data.user_id)
    if user is None:
        raise credentials_exception
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Ensure the authenticated user account is active.

    Args:
        current_user: User resolved by ``get_current_user``.

    Returns:
        The active ``User``.

    Raises:
        HTTPException(403): If the user account is deactivated.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )
    return current_user


async def get_optional_user(
    token: Optional[str] = Depends(OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Optionally resolve the current user from a bearer token.

    This dependency never raises -- it returns ``None`` when no token is
    present or the token is invalid.  Use it for endpoints that behave
    differently for authenticated vs. anonymous callers.

    Args:
        token: Optional bearer token.
        db: Async database session.

    Returns:
        The ``User`` if authentication succeeded, otherwise ``None``.
    """
    if token is None:
        return None
    try:
        token_data = decode_token(token)
    except JWTError:
        return None
    if token_data.token_type != "access":
        return None
    repo = UserRepository(db)
    user = await repo.get_by_id(token_data.user_id)
    if user is None or not user.is_active:
        return None
    return user


def require_role(min_role: UserRole):
    """Dependency factory that enforces a minimum role level.

    The role hierarchy is: viewer < responder < commander < admin.

    Usage::

        @router.delete("/users/{id}")
        async def delete_user(
            user: User = Depends(require_role(UserRole.ADMIN)),
        ):
            ...

    Args:
        min_role: The minimum role required.

    Returns:
        A FastAPI ``Depends``-compatible callable.
    """
    async def _checker(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        user_level = ROLE_HIERARCHY.get(current_user.role, 0)
        required_level = ROLE_HIERARCHY.get(min_role, 0)
        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {min_role.value!r} or higher is required",
            )
        return current_user

    return _checker
