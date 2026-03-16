"""Tests for JWT authentication, password hashing, and RBAC."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jose import JWTError

from src.auth.jwt import (
    JWT_SECRET,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from src.auth.password import hash_password, verify_password
from src.auth.rbac import (
    ROLE_HIERARCHY,
    ROLE_PERMISSIONS,
    Permission,
    has_permission,
)
from src.database.models import UserRole


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------

class TestJWT:
    def _make_claims(self) -> dict:
        return {
            "sub": str(uuid.uuid4()),
            "email": "alice@opslens.dev",
            "role": "admin",
            "org_id": str(uuid.uuid4()),
        }

    def test_create_and_decode_access_token(self):
        claims = self._make_claims()
        token = create_access_token(claims)
        decoded = decode_token(token)
        assert str(decoded.user_id) == claims["sub"]
        assert decoded.email == "alice@opslens.dev"
        assert decoded.role == "admin"
        assert str(decoded.org_id) == claims["org_id"]
        assert decoded.token_type == "access"

    def test_access_token_expiry(self):
        claims = self._make_claims()
        token = create_access_token(claims, expires_delta=timedelta(minutes=30))
        decoded = decode_token(token)
        # Expiry should be roughly 30 min from now
        delta = decoded.exp - datetime.now(timezone.utc)
        assert 29 * 60 < delta.total_seconds() < 31 * 60

    def test_expired_token_raises(self):
        claims = self._make_claims()
        token = create_access_token(claims, expires_delta=timedelta(seconds=-1))
        with pytest.raises(JWTError):
            decode_token(token)

    def test_invalid_token_raises(self):
        with pytest.raises(JWTError):
            decode_token("not.a.valid.token")

    def test_tampered_token_raises(self):
        claims = self._make_claims()
        token = create_access_token(claims)
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(JWTError):
            decode_token(tampered)

    def test_refresh_token_creation(self):
        claims = self._make_claims()
        token = create_refresh_token(claims)
        decoded = decode_token(token)
        assert decoded.token_type == "refresh"
        # Should be valid for days, not minutes
        delta = decoded.exp - datetime.now(timezone.utc)
        assert delta.days >= 6

    def test_uuid_values_serialized(self):
        claims = {
            "sub": uuid.uuid4(),
            "email": "test@test.com",
            "role": "viewer",
            "org_id": uuid.uuid4(),
        }
        token = create_access_token(claims)
        decoded = decode_token(token)
        assert isinstance(decoded.user_id, uuid.UUID)
        assert isinstance(decoded.org_id, uuid.UUID)

    def test_missing_claims_raises(self):
        """Token missing required claims should raise."""
        from jose import jwt as jose_jwt
        incomplete = {"sub": str(uuid.uuid4()), "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
        token = jose_jwt.encode(incomplete, JWT_SECRET, algorithm="HS256")
        with pytest.raises(JWTError, match="missing required claims"):
            decode_token(token)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

class TestPasswordHashing:
    def test_hash_and_verify(self):
        password = "SecureP@ssw0rd!"
        hashed = hash_password(password)
        assert hashed != password
        assert verify_password(password, hashed)

    def test_wrong_password_fails(self):
        hashed = hash_password("correct-horse-battery-staple")
        assert verify_password("wrong-password", hashed) is False

    def test_different_hashes_for_same_password(self):
        """Bcrypt includes a random salt, so hashes differ."""
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert h1 != h2
        # But both should verify
        assert verify_password("same-password", h1)
        assert verify_password("same-password", h2)

    def test_empty_password(self):
        hashed = hash_password("")
        assert verify_password("", hashed)
        assert verify_password("notempty", hashed) is False


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

class TestRBAC:
    def test_viewer_permissions(self):
        assert has_permission(UserRole.VIEWER, Permission.VIEW_INCIDENTS)
        assert not has_permission(UserRole.VIEWER, Permission.CREATE_INCIDENTS)
        assert not has_permission(UserRole.VIEWER, Permission.MANAGE_USERS)

    def test_responder_permissions(self):
        assert has_permission(UserRole.RESPONDER, Permission.VIEW_INCIDENTS)
        assert has_permission(UserRole.RESPONDER, Permission.CREATE_INCIDENTS)
        assert has_permission(UserRole.RESPONDER, Permission.TRANSITION_INCIDENTS)
        assert has_permission(UserRole.RESPONDER, Permission.COMMENT_INCIDENTS)
        assert has_permission(UserRole.RESPONDER, Permission.EXECUTE_RUNBOOKS)
        assert not has_permission(UserRole.RESPONDER, Permission.RUN_COMMANDER)
        assert not has_permission(UserRole.RESPONDER, Permission.MANAGE_USERS)

    def test_commander_permissions(self):
        assert has_permission(UserRole.COMMANDER, Permission.VIEW_INCIDENTS)
        assert has_permission(UserRole.COMMANDER, Permission.CREATE_INCIDENTS)
        assert has_permission(UserRole.COMMANDER, Permission.TRANSITION_INCIDENTS)
        assert has_permission(UserRole.COMMANDER, Permission.RUN_COMMANDER)
        assert has_permission(UserRole.COMMANDER, Permission.MANAGE_ONCALL)
        assert has_permission(UserRole.COMMANDER, Permission.VIEW_AUDIT)
        assert has_permission(UserRole.COMMANDER, Permission.GENERATE_REPORTS)
        assert not has_permission(UserRole.COMMANDER, Permission.MANAGE_USERS)
        assert not has_permission(UserRole.COMMANDER, Permission.MANAGE_SETTINGS)

    def test_admin_has_all_permissions(self):
        for perm in Permission:
            assert has_permission(UserRole.ADMIN, perm), f"Admin should have {perm.value}"

    def test_role_hierarchy_ordering(self):
        assert ROLE_HIERARCHY[UserRole.VIEWER] < ROLE_HIERARCHY[UserRole.RESPONDER]
        assert ROLE_HIERARCHY[UserRole.RESPONDER] < ROLE_HIERARCHY[UserRole.COMMANDER]
        assert ROLE_HIERARCHY[UserRole.COMMANDER] < ROLE_HIERARCHY[UserRole.ADMIN]

    def test_higher_roles_have_more_permissions(self):
        viewer_perms = ROLE_PERMISSIONS[UserRole.VIEWER]
        responder_perms = ROLE_PERMISSIONS[UserRole.RESPONDER]
        commander_perms = ROLE_PERMISSIONS[UserRole.COMMANDER]
        admin_perms = ROLE_PERMISSIONS[UserRole.ADMIN]

        assert viewer_perms < responder_perms
        assert responder_perms < commander_perms
        assert commander_perms < admin_perms

    def test_has_permission_unknown_role_returns_false(self):
        """An unknown role (not in mapping) should get no permissions."""
        # Use a mock value that won't be in ROLE_PERMISSIONS
        assert has_permission("nonexistent_role", Permission.VIEW_INCIDENTS) is False
