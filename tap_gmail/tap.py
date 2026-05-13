"""Gmail tap class."""

from typing import List

from singer_sdk import Stream, Tap
from singer_sdk import typing as th  # JSON schema typing helpers

from tap_gmail.streams import MessageListStream, MessagesStream, UsersStream

STREAM_TYPES = [
    UsersStream,
    MessageListStream,
    MessagesStream,
]


class TapGmail(Tap):
    """Gmail tap class."""

    name = "tap-gmail"
    config_jsonschema = th.PropertiesList(
        th.Property(
            "service_account_credentials",
            th.StringType,
            secret=True,
            required=True,
            description=(
                "JSON content of the Google service account key. The "
                "service account must have Domain-Wide Delegation "
                "enabled with the scopes "
                "'admin.directory.user.readonly' and 'gmail.readonly'."
            ),
        ),
        th.Property(
            "delegated_admin_email",
            th.StringType,
            required=True,
            description=(
                "Email of a Google Workspace admin used to impersonate "
                "when calling the Admin SDK Directory API to list users."
            ),
        ),
        th.Property(
            "excluded_user_emails",
            th.ArrayType(th.StringType),
            default=[],
            description=(
                "List of user emails to exclude from sync (e.g. shared "
                "mailboxes, system accounts, former employees). "
                "Matching is case-insensitive."
            ),
        ),
        th.Property(
            "start_date",
            th.StringType,
            description=(
                "RFC3339 lower bound applied as ``q=after:<epoch>`` on "
                "the initial full sync of each user (until an "
                "internalDate bookmark exists). Optional: if omitted, "
                "the first run pulls every message the user has."
            ),
        ),
        th.Property(
            "messages.q",
            th.StringType,
            description=(
                "Extra Gmail search query appended to every "
                "users.messages.list call. Same format as the Gmail "
                "search box. See "
                "https://developers.google.com/gmail/api/reference/rest/v1/users.messages/list#query-parameters"
            ),
        ),
        th.Property(
            "messages.include_spam_trash",
            th.BooleanType,
            default=False,
            description="Include messages from SPAM and TRASH in the results.",
        ),
    ).to_dict()

    def discover_streams(self) -> List[Stream]:
        """Return a list of discovered streams."""
        return [stream_class(tap=self) for stream_class in STREAM_TYPES]
