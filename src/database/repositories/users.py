"""Async repository for User model."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import AuthProvider, User, UserRole


class UserRepository:
    """Data-access layer for user accounts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        email: str,
        name: str,
        org_id: uuid.UUID,
        *,
        password_hash: Optional[str] = None,
        role: UserRole = UserRole.VIEWER,
        provider: AuthProvider = AuthProvider.LOCAL,
        provider_id: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ) -> User:
        """Create a new user.

        Args:
            email: Unique email address.
            name: Display name.
            org_id: FK to the organization.
            password_hash: Hashed password (nullable for OAuth users).
            role: Authorization role.
            provider: Authentication provider.
            provider_id: External provider user ID.
            avatar_url: URL to the user's avatar image.

        Returns:
            The newly-created ``User``.
        """
        user = User(
            email=email,
            name=name,
            password_hash=password_hash,
            role=role,
            provider=provider,
            provider_id=provider_id,
            avatar_url=avatar_url,
            org_id=org_id,
        )
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def get_by_id(self, id: uuid.UUID) -> User | None:
        """Look up a user by UUID primary key.

        Args:
            id: User UUID.

        Returns:
            The ``User`` or ``None``.
        """
        stmt = select(User).where(User.id == id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """Look up a user by email address.

        Args:
            email: Email to search.

        Returns:
            The ``User`` or ``None``.
        """
        stmt = select(User).where(User.email == email)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_provider(self, provider: str, provider_id: str) -> User | None:
        """Look up a user by OAuth provider and external ID.

        Args:
            provider: Provider name (``"google"``, ``"github"``).
            provider_id: The user's ID in that provider's system.

        Returns:
            The ``User`` or ``None``.
        """
        stmt = select(User).where(
            User.provider == AuthProvider(provider),
            User.provider_id == provider_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_org(self, org_id: uuid.UUID) -> Sequence[User]:
        """Return all users belonging to an organization.

        Args:
            org_id: Organization UUID.

        Returns:
            Sequence of ``User`` objects ordered by name.
        """
        stmt = (
            select(User)
            .where(User.org_id == org_id, User.is_active.is_(True))
            .order_by(User.name.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update(self, id: uuid.UUID, **fields: Any) -> User | None:
        """Update arbitrary fields on a user.

        Args:
            id: User UUID.
            **fields: Column names and new values.

        Returns:
            The updated ``User`` or ``None`` if not found.
        """
        user = await self.get_by_id(id)
        if user is None:
            return None
        for key, value in fields.items():
            setattr(user, key, value)
        user.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def deactivate(self, id: uuid.UUID) -> None:
        """Soft-delete a user by setting ``is_active = False``.

        Args:
            id: User UUID.
        """
        stmt = (
            update(User)
            .where(User.id == id)
            .values(is_active=False, updated_at=datetime.now(timezone.utc))
        )
        await self._session.execute(stmt)
        await self._session.flush()
