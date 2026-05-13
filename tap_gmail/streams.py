"""Stream type classes for tap-gmail."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from tap_gmail.client import DirectoryStream, GmailStream

SCHEMAS_DIR = Path(__file__).parent / Path("./schemas")


class UsersStream(DirectoryStream):
    """List active users of the Google Workspace tenant.

    Acts as the parent stream: each emitted user becomes the context for
    a child ``MessageListStream`` sync.
    """

    name = "gmail_users"
    path = "/users"
    primary_keys = ["id"]
    records_jsonpath = "$.users[*]"
    next_page_token_jsonpath = "$.nextPageToken"
    schema_filepath = SCHEMAS_DIR / "users.json"

    def get_url_params(
        self, context: Optional[dict], next_page_token: Optional[Any]
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            # 'my_customer' resolves to the tenant of the delegated admin.
            "customer": "my_customer",
            "maxResults": 500,
            "projection": "basic",
            "query": "isSuspended=false",
            "showDeleted": False,
        }
        if next_page_token:
            params["pageToken"] = next_page_token
        return params

    def parse_response(self, response) -> Iterable[dict]:
        payload = response.json()
        excluded = {
            email.lower()
            for email in (self.config.get("excluded_user_emails") or [])
        }
        for user in payload.get("users", []):
            if user.get("suspended") or user.get("archived"):
                continue
            email = (user.get("primaryEmail") or "").lower()
            if email in excluded:
                self.logger.info(
                    "Excluding user %s (matched excluded_user_emails)",
                    email,
                )
                continue
            yield user

    def get_child_context(self, record: dict, context: Optional[dict]) -> dict:
        return {
            "user_id": record["id"],
            "user_email": record["primaryEmail"],
        }


class MessageListStream(GmailStream):
    """List message IDs for a given user, filtered incrementally.

    No replication key here: state is only kept on ``MessagesStream``
    (the leaf that has ``internalDate``). On each run we read the
    previous run's bookmark for the same user and translate it into a
    Gmail ``q=after:<epoch_seconds>`` filter, mirroring the strategy
    used before the service-account migration.
    """

    name = "gmail_message_list"
    primary_keys = ["id"]
    replication_key = None
    schema_filepath = SCHEMAS_DIR / "message_list.json"
    records_jsonpath = "$.messages[*]"
    next_page_token_jsonpath = "$.nextPageToken"

    parent_stream_type = UsersStream
    state_partitioning_keys = ["user_id"]

    @property
    def path(self) -> str:
        # The impersonated subject is the user themselves, so ``me`` is
        # the right alias regardless of which user we're syncing.
        return "/gmail/v1/users/me/messages"

    def get_child_context(self, record: dict, context: Optional[dict]) -> dict:
        return {
            "user_id": context["user_id"],
            "user_email": context["user_email"],
            "message_id": record["id"],
        }

    def post_process(
        self, row: dict, context: Optional[dict] = None
    ) -> Optional[dict]:
        """Inject the impersonated user's identity."""
        if context:
            row["user_id"] = context.get("user_id")
            row["user_email"] = context.get("user_email")
        return row

    def _last_internal_date_ms(self, context: Optional[dict]) -> Optional[int]:
        """Look up the previous run's bookmark for this user on the
        sibling ``gmail_messages`` stream.

        ``gmail_messages`` is partitioned by ``user_id`` so we ask its
        per-partition state via ``get_context_state`` — same context
        keys, different stream.
        """
        messages_stream = self._tap.streams.get("gmail_messages")
        if messages_stream is None or not context:
            return None
        state = messages_stream.get_context_state(context) or {}
        # We deliberately IGNORE progress_markers. Gmail returns messages
        # newest-first. If a partition crashes midway, progress_markers
        # will contain the newest date, but we haven't successfully
        # synced the older messages. Ignoring progress_markers ensures
        # we resume from the last fully completed partition state.
        value = state.get("replication_key_value")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            self.logger.warning(
                "Unexpected replication_key_value %r for %s; "
                "ignoring incremental filter.",
                value,
                context,
            )
            return None

    def _start_date_epoch_seconds(self) -> Optional[int]:
        start_date = self.config.get("start_date")
        if not start_date:
            return None
        try:
            # Singer convention: RFC3339 / ISO 8601.
            dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        except ValueError:
            self.logger.warning(
                "start_date %r is not a valid ISO 8601 timestamp; ignoring.",
                start_date,
            )
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

    def get_url_params(
        self, context: Optional[dict], next_page_token: Optional[Any]
    ) -> Dict[str, Any]:
        params = super().get_url_params(context, next_page_token)
        if next_page_token:
            params["pageToken"] = next_page_token

        params["includeSpamTrash"] = self.config.get(
            "messages.include_spam_trash", False
        )

        q_parts = []
        base_q = self.config.get("messages.q")
        if base_q:
            q_parts.append(base_q)

        last_ms = self._last_internal_date_ms(context)
        if last_ms is not None:
            # Gmail ``after:`` expects epoch seconds.
            q_parts.append(f"after:{last_ms // 1000}")
        else:
            start_epoch = self._start_date_epoch_seconds()
            if start_epoch is not None:
                q_parts.append(f"after:{start_epoch}")

        if q_parts:
            params["q"] = " ".join(q_parts)
        return params


class MessagesStream(GmailStream):
    """Fetch full message payloads, one per ID emitted by the parent."""

    name = "gmail_messages"
    primary_keys = ["id"]
    replication_key = "internalDate"
    schema_filepath = SCHEMAS_DIR / "messages.json"

    parent_stream_type = MessageListStream
    ignore_parent_replication_key = True
    state_partitioning_keys = ["user_id"]

    # ``internalDate`` is a stringified epoch ms — Gmail returns
    # messages newest-first, so the default sorted-check would
    # complain. We rely on ``replication_method = INCREMENTAL`` +
    # the SDK's max-tracking to advance the bookmark monotonically.
    check_sorted = False

    @property
    def path(self) -> str:
        return "/gmail/v1/users/me/messages/{message_id}"

    def post_process(
        self, row: dict, context: Optional[dict] = None
    ) -> Optional[dict]:
        payload = row.pop("payload", None)
        if payload is not None:
            row["headers"] = payload.get("headers", [])
        if context:
            row["user_id"] = context.get("user_id")
            row["user_email"] = context.get("user_email")
        return row
