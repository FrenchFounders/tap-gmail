"""tap-gmail Authentication.

Built on Singer SDK's ``OAuthJWTAuthenticator``: a Google service
account with Domain-Wide Delegation is a textbook OAuth2 JWT-bearer
client. We reuse the framework's machinery for assertion signing,
token caching, expiry detection and refresh, and only override what's
specific to Google service accounts:

  - ``client_id`` / ``private_key`` come from the service account
    JSON key file (instead of two separate config entries).
  - ``oauth_request_body`` adds the ``sub`` claim that identifies the
    impersonated end user (Domain-Wide Delegation).

The authenticator is keyed per impersonated subject so each user has
its own access-token cache and refresh lifecycle, exactly like a
``SingletonMeta`` authenticator would for a single-tenant tap.
"""

from __future__ import annotations

import json
from typing import Dict, Optional, Tuple, Union

import backoff
import requests
from singer_sdk.authenticators import OAuthJWTAuthenticator

DIRECTORY_SCOPES: Tuple[str, ...] = (
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
)
GMAIL_SCOPES: Tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
)


def _load_service_account_info(raw: Union[str, dict]) -> dict:
    """Accept either a JSON string or a dict and return the parsed info."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    raise TypeError(
        "service_account_credentials must be a JSON string or a dict, "
        f"got {type(raw)!r}"
    )


def _is_permanent_token_error(exc: Exception) -> bool:
    """Give up on 4xx (bad grant, unknown subject); retry 5xx, 429 and network errors."""
    # The SDK wraps the HTTPError in a RuntimeError, or masks it with a
    # JSONDecodeError when the 5xx body isn't JSON.
    http_error = (
        exc
        if isinstance(exc, requests.HTTPError)
        else exc.__cause__ or exc.__context__
    )
    if not isinstance(http_error, requests.HTTPError) or http_error.response is None:
        return False
    status = http_error.response.status_code
    return status < 500 and status != 429


class GmailAuthenticator(OAuthJWTAuthenticator):
    """OAuth2 JWT authenticator for Google service accounts with DWD."""

    # Per-(subject, scopes) registry. ``OAuthJWTAuthenticator`` already
    # caches the access token internally; we just need one instance per
    # impersonated user so they don't trample on each other's tokens.
    _registry: Dict[
        Tuple[str, Tuple[str, ...]], "GmailAuthenticator"
    ] = {}

    def __init__(
        self,
        stream,
        subject: str,
        scopes: Tuple[str, ...],
    ) -> None:
        self._sa_info = _load_service_account_info(
            stream.config["service_account_credentials"]
        )
        self._subject = subject
        super().__init__(
            stream=stream,
            auth_endpoint=self._sa_info.get(
                "token_uri", "https://oauth2.googleapis.com/token"
            ),
            oauth_scopes=" ".join(scopes),
        )

    @classmethod
    def create_for_subject(
        cls,
        stream,
        subject: str,
        scopes: Tuple[str, ...],
    ) -> "GmailAuthenticator":
        """Return (or build) the authenticator for a given impersonated user."""
        key = (subject, tuple(scopes))
        instance = cls._registry.get(key)
        if instance is None:
            instance = cls(stream, subject, scopes)
            cls._registry[key] = instance
        return instance

    # --- OAuthJWTAuthenticator hooks --------------------------------------

    @property
    def client_id(self) -> str:
        return self._sa_info["client_email"]

    @property
    def private_key(self) -> str:
        return self._sa_info["private_key"]

    @property
    def private_key_passphrase(self) -> Optional[str]:
        return None

    @property
    def oauth_request_body(self) -> dict:
        """Add the ``sub`` claim for Domain-Wide Delegation."""
        body = super().oauth_request_body
        body["sub"] = self._subject
        return body

    # Token refresh runs in ``prepare_request``, outside RESTStream's
    # backoff, so a single 5xx from Google's token endpoint kills the run.
    @backoff.on_exception(
        backoff.expo,
        (RuntimeError, requests.RequestException),
        max_tries=5,
        factor=2,
        giveup=_is_permanent_token_error,
    )
    def update_access_token(self) -> None:
        super().update_access_token()
