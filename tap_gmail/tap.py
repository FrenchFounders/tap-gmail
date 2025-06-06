"""Gmail tap class."""

from typing import Dict, List, Any, Optional
from singer_sdk import Stream, Tap
from singer_sdk import typing as th  # JSON schema typing helpers
from singer_sdk._singerlib import Catalog, StateMessage, write_message

from tap_gmail.streams import GmailStream, MessageListStream, MessagesStream

STREAM_TYPES = [MessageListStream, MessagesStream]

def is_correct_state(state: Dict[str, Any]):
    return (
            state != {}
            and "gmail_messages" in state["bookmarks"]
            and state["bookmarks"]["gmail_messages"] != {}
            and "gmail_message_list" in state["bookmarks"]
        )

class TapGmail(Tap):
    """Gmail tap class."""

    name = "tap-gmail"
    config_jsonschema = th.PropertiesList(
        th.Property(
            "oauth_credentials.client_id",
            th.StringType,
            description="Your google client_id",
        ),
        th.Property(
            "oauth_credentials.client_secret",
            th.StringType,
            secret=True,
            description="Your google client_secret",
        ),
        th.Property(
            "oauth_credentials.refresh_token",
            th.StringType,
            secret=True,
            description="Your google refresh token",
        ),
        th.Property(
            "messages.q",
            th.StringType,
            description="Only return messages matching the specified query. Supports the same query format as the Gmail search box. For example, \"from:someuser@example.com rfc822msgid:<somemsgid@example.com> is:unread\". Parameter cannot be used when accessing the api using the gmail.metadata scope. https://developers.google.com/gmail/api/reference/rest/v1/users.messages/list#query-parameters",
        ),
        th.Property(
            "user_id", 
            th.StringType, 
            description="Your Gmail User ID"
        ),
        th.Property(
            "messages.include_spam_trash",
            th.BooleanType,
            description="Include messages from SPAM and TRASH in the results.",
            default=False,
        ),
    ).to_dict()
    
    def load_state(self, state: dict[str, Any]) -> None:
        # Since MessageStream is child and it contains the state to apply to MessageListStream, we extract the replication_key_value here to bypass this limitation
        super().load_state(state)
        self.logger.info(state)
        if is_correct_state(state):
            if "progress_markers" not in state["bookmarks"]["gmail_messages"]:
                self.replication_key_value = state["bookmarks"]["gmail_messages"]["replication_key_value"]
            else:
                self.replication_key_value = state["bookmarks"]["gmail_messages"]["progress_markers"]["replication_key_value"]
            write_message(StateMessage(state))
        else:
            self.replication_key_value = None
        
    def discover_streams(self) -> List[Stream]:
        """Return a list of discovered streams."""
        return [stream_class(tap=self) for stream_class in STREAM_TYPES]
