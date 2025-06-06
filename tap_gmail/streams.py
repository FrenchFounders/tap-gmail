"""Stream type classes for tap-gmail."""

from pathlib import Path
from typing import Any, Dict, Optional

from tap_gmail.client import GmailStream

SCHEMAS_DIR = Path(__file__).parent / Path("./schemas")


class MessageListStream(GmailStream):
    """Define custom stream."""

    name = "gmail_message_list"
    primary_keys = ["id"]
    replication_key = None
    schema_filepath = SCHEMAS_DIR / "message_list.json"
    records_jsonpath = "$.messages[*]"
    next_page_token_jsonpath = "$.nextPageToken"

    @property
    def path(self):
        """Set the path for the stream."""
        return "/gmail/v1/users/" + self.config["user_id"] + "/messages"

    def get_child_context(self, record: dict, context: Optional[dict]) -> dict:
        """Return a context dictionary for child streams."""
        return {"message_id": record["id"]}

    def get_url_params(
        self, context: Optional[dict], next_page_token: Optional[Any]
    ) -> Dict[str, Any]:
        params = super().get_url_params(context, next_page_token)
        params["includeSpamTrash"]=self.config["messages.include_spam_trash"]

        params["q"] = self.config.get("messages", {}).get("q","")
        if self._tap.replication_key_value != None:
            params["q"]+= f" after:{str(int(self._tap.replication_key_value)/1000)}"
        return params


class MessagesStream(GmailStream):

    name = "gmail_messages"
    replication_key = "internalDate"
    primary_keys = ["id"]
    schema_filepath = SCHEMAS_DIR / "messages.json"
    parent_stream_type = MessageListStream
    ignore_parent_replication_keys = True
    state_partitioning_keys = []

    @property
    def path(self):
        """Set the path for the stream."""
        return "/gmail/v1/users/" + self.config["user_id"] + "/messages/{message_id}"

    def post_process(self, row: dict, context: dict):
        payload = row.pop('payload')
        row['headers'] = payload['headers']
        row['user_id'] = self.config["user_id"]

        return row
