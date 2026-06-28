from typing import Any

from app.models.domain import WhatsAppInboundMessage

SUPPORTED_MESSAGE_TYPES = {"text", "interactive"}


def parse_inbound_messages(payload: dict[str, Any]) -> list[WhatsAppInboundMessage]:
    messages: list[WhatsAppInboundMessage] = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts_by_wa_id = {
                contact.get("wa_id"): contact.get("profile", {}).get("name")
                for contact in value.get("contacts", [])
            }
            phone_number_id = value.get("metadata", {}).get("phone_number_id")

            for message in value.get("messages", []):
                message_type = message.get("type")
                if message_type not in SUPPORTED_MESSAGE_TYPES:
                    continue

                remoteJid = message.get("from")
                message_id = message.get("id")

                text = message.get("text", {}).get("body")
                if not remoteJid or not message_id or text is None:
                    continue

                if message_type == "text":
                    text = message.get("text", {}).get("body")
                    if text is None:
                        continue
                    messages.append(
                        WhatsAppInboundMessage(
                            message_id=message_id,
                            remoteJid=remoteJid,
                            text=text,
                            timestamp=message.get("timestamp"),
                            profile_name=contacts_by_wa_id.get(remoteJid),
                            phone_number_id=phone_number_id,
                            message_type="text",
                            raw=message,
                        )
                    )
                    continue

                interactive = message.get("interactive", {})
                if interactive.get("type") != "list_reply":
                    continue

                list_reply = interactive.get("list_reply", {})
                reply_id = list_reply.get("id")
                if not reply_id:
                    continue

                title = list_reply.get("title") or reply_id
                messages.append(
                    WhatsAppInboundMessage(
                        message_id=message_id,
                        remoteJid=remoteJid,
                        text=title,
                        timestamp=message.get("timestamp"),
                        profile_name=contacts_by_wa_id.get(remoteJid),
                        phone_number_id=phone_number_id,
                        message_type="interactive",
                        interactive_reply_id=reply_id,
                        raw=message,
                    )
                )

    return messages
