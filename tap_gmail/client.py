"""REST client handling, including GmailStream base class."""

from pathlib import Path
from typing import Any, Optional

import requests
from singer_sdk.streams import RESTStream

from tap_gmail.auth import (
    DIRECTORY_SCOPES,
    GMAIL_SCOPES,
    GmailAuthenticator,
)

SCHEMAS_DIR = Path(__file__).parent / Path("./schemas")


class GoogleAPIStream(RESTStream):
    """Base stream for any Google API call using the service account.

    The impersonated subject depends on context (the admin for the
    Directory API, the end user for the Gmail API) so we resolve the
    authenticator per request rather than via the standard
    ``authenticator`` property — but we still use the Singer SDK
    ``OAuthJWTAuthenticator`` machinery via ``auth_headers``.
    """

    #: OAuth scopes required by the subclass.
    scopes: tuple = ()

    @property
    def http_headers(self) -> dict:
        headers = {}
        if "user_agent" in self.config:
            headers["User-Agent"] = self.config.get("user_agent")
        return headers

    @property
    def authenticator(self):
        # Disabled: the impersonated subject is context-dependent, so
        # the authenticator is resolved in ``prepare_request`` below.
        return None

    def get_impersonated_subject(self, context: Optional[dict]) -> str:
        """Return the email of the user to impersonate for this request."""
        raise NotImplementedError

    def prepare_request(
        self, context: Optional[dict], next_page_token: Optional[Any]
    ) -> requests.PreparedRequest:
        request = super().prepare_request(context, next_page_token)
        subject = self.get_impersonated_subject(context)
        authenticator = GmailAuthenticator.create_for_subject(
            stream=self,
            subject=subject,
            scopes=self.scopes,
        )
        # ``auth_headers`` triggers ``update_access_token`` if the
        # cached token is expired — full framework lifecycle, no
        # bespoke refresh logic.
        request.headers.update(authenticator.auth_headers)
        return request

    def validate_response(
        self, response: requests.Response, context=None
    ) -> None:
        # Surface the Google response body on 4xx — the default Singer
        # SDK error message only carries the HTTP status, which makes
        # debugging Google's authorization layer painful.
        if 400 <= response.status_code < 500:
            body = response.text[:1000] if response.text else "<empty>"
            self.logger.error(
                "Google API %s on %s: %s",
                response.status_code,
                response.request.url if response.request else response.url,
                body,
            )
        return super().validate_response(response)


class GmailStream(GoogleAPIStream):
    """Stream class for endpoints under the Gmail API."""

    url_base = "https://gmail.googleapis.com"
    scopes = GMAIL_SCOPES

    def get_impersonated_subject(self, context: Optional[dict]) -> str:
        if not context or "user_email" not in context:
            raise RuntimeError(
                "GmailStream requires a context with 'user_email' "
                "(provided by the parent UsersStream)."
            )
        return context["user_email"]


class DirectoryStream(GoogleAPIStream):
    """Stream class for the Admin SDK Directory API."""

    url_base = "https://admin.googleapis.com/admin/directory/v1"
    scopes = DIRECTORY_SCOPES

    def get_impersonated_subject(self, context: Optional[dict]) -> str:
        admin_email = self.config.get("delegated_admin_email")
        if not admin_email:
            raise RuntimeError(
                "delegated_admin_email must be configured to call the "
                "Admin SDK Directory API."
            )
        return admin_email
