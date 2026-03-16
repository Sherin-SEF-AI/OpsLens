"""OpsLens authentication and authorization package."""

from src.auth.jwt import create_access_token, create_refresh_token, decode_token, TokenData
from src.auth.password import hash_password, verify_password
from src.auth.rbac import Permission, has_permission, require_permission, ROLE_PERMISSIONS
from src.auth.middleware import (
    get_current_user,
    get_current_active_user,
    get_optional_user,
    require_role,
    oauth2_scheme,
)

__all__ = [
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "TokenData",
    "hash_password",
    "verify_password",
    "Permission",
    "has_permission",
    "require_permission",
    "ROLE_PERMISSIONS",
    "get_current_user",
    "get_current_active_user",
    "get_optional_user",
    "require_role",
    "oauth2_scheme",
]
