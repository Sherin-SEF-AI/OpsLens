"""Authentication and user-management API router for OpsLens."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.jwt import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from src.auth.middleware import get_current_active_user, require_role
from src.auth.oauth import get_oauth_provider
from src.auth.password import hash_password, verify_password
from src.auth.rbac import Permission, has_permission, require_permission
from src.database.engine import get_db
from src.database.models import AuthProvider, Organization, User, UserRole
from src.database.repositories.users import UserRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# In-memory token blacklist (swap for Redis / DB in production at scale)
# ---------------------------------------------------------------------------
_blacklisted_tokens: set[str] = set()


# ---------------------------------------------------------------------------
# Pydantic request / response schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    """Body for user registration."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    name: str = Field(..., min_length=1, max_length=255)


class LoginRequest(BaseModel):
    """Body for email + password login."""

    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    """Body for token refresh."""

    refresh_token: str


class ChangePasswordRequest(BaseModel):
    """Body for password change."""

    old_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


class UpdateProfileRequest(BaseModel):
    """Body for profile update."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    avatar_url: Optional[str] = None


class UpdateUserRoleRequest(BaseModel):
    """Body for admin role update."""

    role: UserRole


class TokenResponse(BaseModel):
    """JWT token pair returned after authentication."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Public user representation."""

    id: uuid.UUID
    email: str
    name: str
    avatar_url: Optional[str] = None
    role: str
    provider: str
    is_active: bool
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


class OAuthAuthorizeResponse(BaseModel):
    """OAuth authorization URL response."""

    authorization_url: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_token_claims(user: User) -> dict:
    """Build the JWT claims dict from a User model."""
    return {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value,
        "org_id": str(user.org_id),
    }


async def _get_or_create_default_org(db: AsyncSession) -> uuid.UUID:
    """Return the default organisation, creating one if none exists.

    This keeps registration simple for single-tenant deployments.
    """
    result = await db.execute(select(Organization).limit(1))
    org = result.scalar_one_or_none()
    if org is not None:
        return org.id

    org = Organization(
        name="Default Organization",
        slug="default",
    )
    db.add(org)
    await db.flush()
    await db.refresh(org)
    return org.id


# ---------------------------------------------------------------------------
# Registration & Login
# ---------------------------------------------------------------------------

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Register a new local user account.

    Creates the user with the ``viewer`` role.  The first user registered
    in the system is automatically promoted to ``admin``.

    Returns an access / refresh token pair on success.
    """
    repo = UserRepository(db)

    existing = await repo.get_by_email(body.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    org_id = await _get_or_create_default_org(db)

    # Check if this is the first user -- make them admin
    result = await db.execute(select(User).limit(1))
    is_first_user = result.scalar_one_or_none() is None
    role = UserRole.ADMIN if is_first_user else UserRole.VIEWER

    user = await repo.create(
        email=body.email,
        name=body.name,
        org_id=org_id,
        password_hash=hash_password(body.password),
        role=role,
        provider=AuthProvider.LOCAL,
    )

    claims = _build_token_claims(user)
    return TokenResponse(
        access_token=create_access_token(claims),
        refresh_token=create_refresh_token(claims),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate with email and password.

    Returns an access / refresh token pair.
    """
    repo = UserRepository(db)
    user = await repo.get_by_email(body.email)

    if user is None or user.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    claims = _build_token_claims(user)
    return TokenResponse(
        access_token=create_access_token(claims),
        refresh_token=create_refresh_token(claims),
    )


# ---------------------------------------------------------------------------
# Token Management
# ---------------------------------------------------------------------------

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Exchange a valid refresh token for a new access / refresh pair."""
    if body.refresh_token in _blacklisted_tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    try:
        token_data = decode_token(body.refresh_token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    if token_data.token_type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Expected a refresh token",
        )

    repo = UserRepository(db)
    user = await repo.get_by_id(token_data.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated",
        )

    # Blacklist the old refresh token (one-time use)
    _blacklisted_tokens.add(body.refresh_token)

    claims = _build_token_claims(user)
    return TokenResponse(
        access_token=create_access_token(claims),
        refresh_token=create_refresh_token(claims),
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    body: RefreshRequest,
    current_user: User = Depends(get_current_active_user),
) -> MessageResponse:
    """Blacklist the supplied refresh token, effectively logging the user out."""
    _blacklisted_tokens.add(body.refresh_token)
    return MessageResponse(message="Logged out successfully")


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_active_user),
) -> UserResponse:
    """Return the profile of the currently authenticated user."""
    return UserResponse.model_validate(current_user)


@router.put("/me", response_model=UserResponse)
async def update_me(
    body: UpdateProfileRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Update the current user's profile (name and/or avatar)."""
    repo = UserRepository(db)
    updates: dict = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.avatar_url is not None:
        updates["avatar_url"] = body.avatar_url

    if not updates:
        return UserResponse.model_validate(current_user)

    user = await repo.update(current_user.id, **updates)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return UserResponse.model_validate(user)


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Change the current user's password.

    Requires the correct current password.  Only available for local
    (non-OAuth) accounts.
    """
    if current_user.provider != AuthProvider.LOCAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password change is only available for local accounts",
        )
    if current_user.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No password set for this account",
        )
    if not verify_password(body.old_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    repo = UserRepository(db)
    await repo.update(current_user.id, password_hash=hash_password(body.new_password))
    return MessageResponse(message="Password changed successfully")


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

@router.get("/oauth/{provider}/authorize", response_model=OAuthAuthorizeResponse)
async def oauth_authorize(provider: str) -> OAuthAuthorizeResponse:
    """Get the OAuth authorization URL for the requested provider.

    Supported providers: ``google``, ``github``.
    """
    try:
        oauth = get_oauth_provider(provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    url = oauth.get_authorization_url()
    return OAuthAuthorizeResponse(authorization_url=url)


@router.get("/oauth/{provider}/callback", response_model=TokenResponse)
async def oauth_callback(
    provider: str,
    code: str,
    state: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Handle the OAuth provider callback.

    Exchanges the authorization code for user information, then either
    finds the existing user or creates a new one.  Returns a token pair.
    """
    try:
        oauth = get_oauth_provider(provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    try:
        oauth_info = await oauth.exchange_code(code)
    except Exception as exc:
        logger.exception("OAuth code exchange failed for provider=%s", provider)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth authentication failed: {exc}",
        )

    repo = UserRepository(db)

    # Try to find user by provider + provider_id first
    user = await repo.get_by_provider(oauth_info.provider, oauth_info.provider_id)

    if user is None:
        # Check if a local user with the same email exists -- link accounts
        user = await repo.get_by_email(oauth_info.email)
        if user is not None:
            # Update the existing user with OAuth provider info
            await repo.update(
                user.id,
                provider=AuthProvider(oauth_info.provider),
                provider_id=oauth_info.provider_id,
                avatar_url=oauth_info.avatar_url or user.avatar_url,
            )
            # Re-fetch to get updated fields
            user = await repo.get_by_id(user.id)
        else:
            # Brand-new user
            org_id = await _get_or_create_default_org(db)

            # First user becomes admin
            result = await db.execute(select(User).limit(1))
            is_first_user = result.scalar_one_or_none() is None
            role = UserRole.ADMIN if is_first_user else UserRole.VIEWER

            user = await repo.create(
                email=oauth_info.email,
                name=oauth_info.name,
                org_id=org_id,
                provider=AuthProvider(oauth_info.provider),
                provider_id=oauth_info.provider_id,
                avatar_url=oauth_info.avatar_url,
                role=role,
            )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    claims = _build_token_claims(user)
    return TokenResponse(
        access_token=create_access_token(claims),
        refresh_token=create_refresh_token(claims),
    )


# ---------------------------------------------------------------------------
# Admin: User Management
# ---------------------------------------------------------------------------

@router.get("/users", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    """List all users in the current user's organization.

    Requires the ``admin`` role.
    """
    repo = UserRepository(db)
    users = await repo.list_by_org(current_user.org_id)
    return [UserResponse.model_validate(u) for u in users]


@router.put("/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: uuid.UUID,
    body: UpdateUserRoleRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Update another user's role.

    Requires the ``admin`` role.  Admins cannot demote themselves.
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role",
        )

    repo = UserRepository(db)
    target = await repo.get_by_id(user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    if target.org_id != current_user.org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    updated = await repo.update(user_id, role=body.role)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return UserResponse.model_validate(updated)
