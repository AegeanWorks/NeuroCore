"""
Email Bridge node executors.

EmailInputExecutor  — gates Repeater triggers; passes email payload through.
EmailOutputExecutor — sends flow content as a reply email via SMTP.
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

MODULE_JSON = os.path.join(os.path.dirname(__file__), "module.json")


class _ConfigLoader:
    @staticmethod
    def get_config() -> dict:
        try:
            with open(MODULE_JSON, "r", encoding="utf-8") as f:
                return json.load(f).get("config", {})
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(f"email_bridge node: failed to load config: {e}")
            return {}


class EmailInputExecutor:
    """Gate-keeps Repeater triggers; optionally filters by sender.

    Node config keys (set in the flow editor):
        filter_sender: str  — only accept emails from this address.
                              Empty = accept all.
    """

    async def receive(self, input_data: dict, config: dict = None) -> Optional[dict]:
        # Block Repeater-triggered executions
        if input_data.get("_repeat_count", 0) > 0:
            return None

        # Only pass through emails (not other input sources)
        if input_data.get("_input_source") not in (None, "", "email"):
            return None

        # Per-node sender filter
        filter_sender = (config or {}).get("filter_sender", "").strip().lower()
        if filter_sender:
            incoming = input_data.get("_email_from", "").lower()
            if incoming and filter_sender not in incoming:
                return None

        return input_data

    async def send(self, processed_data: dict) -> dict:
        if processed_data and processed_data.get("_repeat_count", 0) > 0:
            return processed_data
        if "messages" not in processed_data and "body" not in processed_data:
            return {
                "error": (
                    "Flow started without 'messages'. "
                    "'Email Input' node requires it."
                )
            }
        return processed_data


class EmailOutputExecutor:
    """Sends flow 'content' as a reply email to the originating sender.

    Node config keys (set in the flow editor):
        to_override: str — send to this address instead of _email_from.
                           Empty = auto (reply to sender).
    """

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        if not input_data or "content" not in input_data:
            return input_data

        content = input_data["content"]
        node_config = config or {}
        to_override = node_config.get("to_override", "").strip()

        recipient = to_override or input_data.get("_email_from", "")
        subject = input_data.get("_email_subject", "")
        message_id = input_data.get("_email_message_id", "")

        if not recipient:
            logger.warning("EmailOutputExecutor: no recipient address — reply skipped.")
            return input_data

        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"

        # Import here to avoid circular import at module load
        from .service import email_service
        smtp = email_service.get_smtp()
        if smtp:
            smtp.send(
                to=recipient,
                subject=reply_subject,
                body=content,
                in_reply_to=message_id or None,
            )
        else:
            logger.warning("EmailOutputExecutor: SMTP bridge not configured — reply skipped.")

        return input_data

    async def send(self, processed_data: dict) -> dict:
        return processed_data


async def get_executor_class(node_type_id: str):
    if node_type_id == "email_input":
        return EmailInputExecutor
    if node_type_id == "email_output":
        return EmailOutputExecutor
    return None
