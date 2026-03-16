"""OAuth2 provider implementations for Google and GitHub."""

from __future__ import annotations

import os
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import httpx


@dataclass
class OAuthUserInfo:
    """Normalised user profile returned by an OAuth provider."""

    email: str
    name: str
    avatar_url: Optional[str]
    provider: str
    provider_id: str


class OAuthProvider(ABC):
    """Base class for OAuth2 authorization-code providers."""

    @abstractmethod
    def get_authorization_url(self, state: Optional[str] = None) -> str:
        """Build the URL that the browser should redirect to.

        Args:
            state: An opaque CSRF token.  If ``None`` one is generated.

        Returns:
            Full authorization URL.
        """

    @abstractmethod
    async def exchange_code(self, code: str) -> OAuthUserInfo:
        """Exchange an authorization code for user information.

        Args:
            code: The code returned by the provider callback.

        Returns:
            Normalised ``OAuthUserInfo``.

        Raises:
            httpx.HTTPStatusError: On upstream API errors.
            ValueError: If the provider response is missing required fields.
        """


# ---------------------------------------------------------------------------
# Google OAuth2
# ---------------------------------------------------------------------------

class GoogleOAuth(OAuthProvider):
    """Google OAuth2 (authorization code flow)."""

    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
    SCOPES = "openid email profile"

    def __init__(self) -> None:
        self.client_id: str = os.environ.get("GOOGLE_CLIENT_ID", "")
        self.client_secret: str = os.environ.get("GOOGLE_CLIENT_SECRET", "")
        self.redirect_base: str = os.environ.get(
            "OAUTH_REDIRECT_BASE_URL", "http://localhost:8000"
        )

    @property
    def redirect_uri(self) -> str:
        return f"{self.redirect_base}/api/auth/oauth/google/callback"

    def get_authorization_url(self, state: Optional[str] = None) -> str:
        """Build the Google authorization URL.

        Args:
            state: CSRF state parameter.

        Returns:
            Google consent screen URL.
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": self.SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "state": state or secrets.token_urlsafe(32),
        }
        return f"{self.AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> OAuthUserInfo:
        """Exchange the Google authorization code for user info.

        Args:
            code: Authorization code from Google callback.

        Returns:
            ``OAuthUserInfo`` populated from the Google userinfo endpoint.

        Raises:
            httpx.HTTPStatusError: If token exchange or userinfo call fails.
            ValueError: If email is missing from userinfo response.
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Exchange code for tokens
            token_resp = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri,
                },
            )
            token_resp.raise_for_status()
            tokens = token_resp.json()
            access_token = tokens["access_token"]

            # Fetch user profile
            userinfo_resp = await client.get(
                self.USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_resp.raise_for_status()
            info = userinfo_resp.json()

        email = info.get("email")
        if not email:
            raise ValueError("Google userinfo response missing email")

        return OAuthUserInfo(
            email=email,
            name=info.get("name", email.split("@")[0]),
            avatar_url=info.get("picture"),
            provider="google",
            provider_id=str(info["id"]),
        )


# ---------------------------------------------------------------------------
# GitHub OAuth2
# ---------------------------------------------------------------------------

class GitHubOAuth(OAuthProvider):
    """GitHub OAuth2 (authorization code flow)."""

    AUTH_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    USER_URL = "https://api.github.com/user"
    EMAILS_URL = "https://api.github.com/user/emails"
    SCOPES = "read:user user:email"

    def __init__(self) -> None:
        self.client_id: str = os.environ.get("GITHUB_CLIENT_ID", "")
        self.client_secret: str = os.environ.get("GITHUB_CLIENT_SECRET", "")
        self.redirect_base: str = os.environ.get(
            "OAUTH_REDIRECT_BASE_URL", "http://localhost:8000"
        )

    @property
    def redirect_uri(self) -> str:
        return f"{self.redirect_base}/api/auth/oauth/github/callback"

    def get_authorization_url(self, state: Optional[str] = None) -> str:
        """Build the GitHub authorization URL.

        Args:
            state: CSRF state parameter.

        Returns:
            GitHub OAuth consent URL.
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.SCOPES,
            "state": state or secrets.token_urlsafe(32),
        }
        return f"{self.AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> OAuthUserInfo:
        """Exchange the GitHub authorization code for user info.

        Args:
            code: Authorization code from GitHub callback.

        Returns:
            ``OAuthUserInfo`` populated from the GitHub user and emails endpoints.

        Raises:
            httpx.HTTPStatusError: If token exchange or user-info call fails.
            ValueError: If no verified primary email is found.
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Exchange code for access token
            token_resp = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                },
                headers={"Accept": "application/json"},
            )
            token_resp.raise_for_status()
            tokens = token_resp.json()
            access_token = tokens["access_token"]

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }

            # Fetch user profile
            user_resp = await client.get(self.USER_URL, headers=headers)
            user_resp.raise_for_status()
            user_data = user_resp.json()

            # Fetch emails (in case profile email is private)
            emails_resp = await client.get(self.EMAILS_URL, headers=headers)
            emails_resp.raise_for_status()
            emails = emails_resp.json()

        # Pick primary verified email
        email: Optional[str] = None
        for entry in emails:
            if entry.get("primary") and entry.get("verified"):
                email = entry["email"]
                break
        # Fallback: first verified email
        if not email:
            for entry in emails:
                if entry.get("verified"):
                    email = entry["email"]
                    break
        # Last resort: profile-level email
        if not email:
            email = user_data.get("email")
        if not email:
            raise ValueError("Could not obtain a verified email from GitHub")

        return OAuthUserInfo(
            email=email,
            name=user_data.get("name") or user_data.get("login", email.split("@")[0]),
            avatar_url=user_data.get("avatar_url"),
            provider="github",
            provider_id=str(user_data["id"]),
        )


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, type[OAuthProvider]] = {
    "google": GoogleOAuth,
    "github": GitHubOAuth,
}


def get_oauth_provider(name: str) -> OAuthProvider:
    """Instantiate an OAuth provider by name.

    Args:
        name: ``"google"`` or ``"github"``.

    Returns:
        An ``OAuthProvider`` instance.

    Raises:
        ValueError: If the provider name is not recognized.
    """
    cls = _PROVIDERS.get(name.lower())
    if cls is None:
        raise ValueError(f"Unknown OAuth provider: {name!r}. Supported: {list(_PROVIDERS)}")
    return cls()
