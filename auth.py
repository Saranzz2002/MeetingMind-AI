"""
auth.py
=======
Google OAuth 2.0 login for Streamlit, with a Guest Mode fallback for
users who don't want to (or can't) authenticate.

Design notes
------------
- Uses `google-auth-oauthlib`'s low-level `Flow` (not the full desktop
  installed-app flow) so we can drive it manually across Streamlit's
  request/rerun cycle: generate an authorization URL, let the user
  click it, and exchange the `code` query param Google redirects back
  with for tokens.
- Client ID/secret are read exclusively from environment variables
  (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`) via config.settings --
  never from a downloaded client_secret.json committed to the repo.
- Scopes are read-only for Calendar, per the "never modify events"
  requirement.
"""

from __future__ import annotations
import os


os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from dataclasses import dataclass
from typing import Optional

from config import logger, settings

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar.readonly",
]

GUEST_EMAIL = "guest"


class AuthError(Exception):
    """Raised when the OAuth flow cannot proceed or complete."""


@dataclass
class AuthenticatedUser:
    email: str
    name: str
    is_guest: bool
    credentials: Optional[object] = None  # google.oauth2.credentials.Credentials


def _build_client_config() -> dict:
    if not settings.google_oauth_configured():
        raise AuthError(
            "Google OAuth is not configured. Set GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET in your .env file, or continue with Guest Mode."
        )
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "project_id": settings.google_project_id,
            "auth_uri": settings.google_auth_uri,
            "token_uri": settings.google_token_uri,
            "auth_provider_x509_cert_url": settings.google_auth_provider_x509_cert_url,
            "redirect_uris": [settings.google_redirect_uri],
        }
    }


class GoogleAuthenticator:
    """Drives the OAuth authorization-code flow across Streamlit reruns."""

    def __init__(self) -> None:
        self._flow = None

    def _get_flow(self):
        from google_auth_oauthlib.flow import Flow

        if self._flow is None:
            self._flow = Flow.from_client_config(
                _build_client_config(),
                scopes=SCOPES,
                redirect_uri=settings.google_redirect_uri,
            )
        return self._flow

    def get_authorization_url(self) -> str:
        """Return the URL the user should visit to grant consent."""
        flow = self._get_flow()
        auth_url, _state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return auth_url

    def exchange_code(self, authorization_response_url: str) -> AuthenticatedUser:
        """Exchange the redirect URL (containing `code=...`) for tokens
        and fetch the user's profile."""
        flow = self._get_flow()
        try:
            flow.fetch_token(authorization_response=authorization_response_url)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Google OAuth token exchange failed.")
            raise AuthError(f"Google login failed: {exc}") from exc

        credentials = flow.credentials

        try:
            import requests

            resp = requests.get(
                settings.google_userinfo_uri,
                headers={"Authorization": f"Bearer {credentials.token}"},
                timeout=10,
            )
            resp.raise_for_status()
            profile = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to fetch Google user profile.")
            raise AuthError(f"Could not fetch Google profile: {exc}") from exc

        logger.info("User %s authenticated via Google OAuth.", profile.get("email"))
        return AuthenticatedUser(
            email=profile.get("email", "unknown"),
            name=profile.get("name", profile.get("email", "Unknown User")),
            is_guest=False,
            credentials=credentials,
        )


def guest_login() -> AuthenticatedUser:
    """Bypass OAuth entirely; used when Google isn't configured or the
    user opts out of signing in. Guest data is scoped under a shared
    'guest' user_email."""
    logger.info("User continued in Guest Mode.")
    return AuthenticatedUser(email=GUEST_EMAIL, name="Guest", is_guest=True, credentials=None)
