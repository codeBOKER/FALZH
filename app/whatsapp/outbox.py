import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.database.supabase import SupabaseRepository
from app.whatsapp.client import WhatsAppClient, WhatsAppClientError

logger = logging.getLogger(__name__)


class WhatsAppOutbox:
    def __init__(
        self,
        *,
        repository: SupabaseRepository,
        client: WhatsAppClient,
        retry_interval_seconds: int,
    ) -> None:
        self.repository = repository
        self.client = client
        self.retry_interval_seconds = retry_interval_seconds

    async def send_text(self, remoteJid: str, text: str) -> dict[str, Any]:
        payload = self.client.build_text_payload(remoteJid, text)
        return await self._enqueue_and_send(remoteJid, payload)

    async def send_interactive_list(
        self,
        remoteJid: str,
        interactive: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self.client.build_interactive_list_payload(remoteJid, interactive)
        return await self._enqueue_and_send(remoteJid, payload)

    async def _enqueue_and_send(
        self,
        recipient: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        row = await self.repository.enqueue_whatsapp_message(
            recipient=recipient,
            payload=payload,
        )
        oldest = await self.repository.get_oldest_active_whatsapp_message()
        if not oldest or oldest.get("id") != row.get("id"):
            return _queued_response(row)

        claimed = await self.repository.claim_whatsapp_message(str(row["id"]))
        if not claimed:
            return _queued_response(row)

        result = await self._deliver_claimed(claimed)
        return result.get("response") or _queued_response(row)

    async def run_retry_worker(self) -> None:
        while True:
            await asyncio.sleep(self.retry_interval_seconds)
            try:
                await self.retry_pending_messages()
            except Exception:
                logger.exception("WhatsApp outbox retry pass failed")

    async def retry_pending_messages(self) -> int:
        lease_seconds = max(self.retry_interval_seconds * 2, 30)
        stale_before = datetime.now(UTC) - timedelta(seconds=lease_seconds)
        await self.repository.recover_stale_whatsapp_messages(stale_before=stale_before)

        oldest = await self.repository.get_oldest_active_whatsapp_message()
        if not oldest or oldest.get("status") != "pending":
            return 0
        if _is_future(oldest.get("next_attempt_at")):
            return 0

        claimed = await self.repository.claim_whatsapp_message(str(oldest["id"]))
        if not claimed:
            return 0

        sent = 0
        result = await self._deliver_claimed(claimed)
        sent += int(result["sent"])
        if result["retryable"]:
            return sent

        while True:
            next_message = await self.repository.get_oldest_active_whatsapp_message()
            if not next_message or next_message.get("status") != "pending":
                break

            claimed = await self.repository.claim_whatsapp_message(
                str(next_message["id"]),
                ignore_retry_time=True,
            )
            if not claimed:
                break

            result = await self._deliver_claimed(claimed)
            sent += int(result["sent"])
            if result["retryable"]:
                break

        return sent

    async def _deliver_claimed(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.send_payload(row["payload"])
        except Exception as exc:  # noqa: BLE001
            retryable = not isinstance(exc, WhatsAppClientError) or exc.retryable
            retry_at = datetime.now(UTC) + timedelta(seconds=self.retry_interval_seconds)
            await self.repository.fail_whatsapp_message(
                str(row["id"]),
                error=str(exc),
                retryable=retryable,
                retry_at=retry_at,
            )
            logger.warning(
                "WhatsApp delivery failed for outbox message %s (retryable=%s): %s",
                row["id"],
                retryable,
                exc,
            )
            return {
                "sent": False,
                "retryable": retryable,
                "response": _queued_response(row, error=str(exc), retryable=retryable),
            }

        await self.repository.mark_whatsapp_message_sent(
            str(row["id"]),
            provider_response=response,
        )
        return {"sent": True, "retryable": False, "response": response}


def _queued_response(
    row: dict[str, Any],
    *,
    error: str | None = None,
    retryable: bool = True,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "messages": [{}],
        "queued": retryable,
        "failed": not retryable,
        "outbox_id": row.get("id"),
    }
    if error:
        response["error"] = error
    return response


def _is_future(value: Any) -> bool:
    if not value:
        return False
    try:
        scheduled = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=UTC)
    return scheduled > datetime.now(UTC)
