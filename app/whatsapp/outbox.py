import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import WhatsAppOutboxLevel
from app.database.supabase import SupabaseRepository
from app.whatsapp.client import WhatsAppClient, WhatsAppClientError

logger = logging.getLogger(__name__)


class WhatsAppOutbox:
    def __init__(
        self,
        *,
        repository: SupabaseRepository,
        client: WhatsAppClient,
        levels: list[WhatsAppOutboxLevel],
        request_timeout_seconds: float,
    ) -> None:
        self.repository = repository
        self.client = client
        self.levels = levels
        self.request_timeout_seconds = request_timeout_seconds
        self.poll_interval_seconds = min(
            level.retry_interval_seconds
            for level in levels
            if level.retry_interval_seconds is not None
        )
        self._send_lock = asyncio.Lock()

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
        async with self._send_lock:
            await self._recover_and_discard_expired()
            if await self.repository.has_sending_whatsapp_message():
                return _queued_response(row)

            candidate, _ = await self._next_pending_message(datetime.now(UTC))
            if not candidate or candidate.get("id") != row.get("id"):
                return _queued_response(row)

            if _is_future(candidate.get("next_attempt_at")):
                return _queued_response(row)

            claimed = await self.repository.claim_whatsapp_message(str(row["id"]))
            if not claimed:
                return _queued_response(row)

            result = await self._deliver_claimed(claimed)
            if not result["retryable"]:
                await self._drain_ready_messages(force=True)
            return result.get("response") or _queued_response(row)

    async def run_retry_worker(self) -> None:
        while True:
            await asyncio.sleep(self.poll_interval_seconds)
            try:
                await self.retry_pending_messages()
            except Exception:
                logger.exception("WhatsApp outbox retry pass failed")

    async def retry_pending_messages(self) -> int:
        async with self._send_lock:
            await self._recover_and_discard_expired()
            if await self.repository.has_sending_whatsapp_message():
                return 0

            candidate, level = await self._next_pending_message(datetime.now(UTC))
            if not candidate or level is None:
                return 0
            await self._align_retry_schedule(candidate, level)
            if _is_future(candidate.get("next_attempt_at")):
                return 0

            claimed = await self.repository.claim_whatsapp_message(str(candidate["id"]))
            if not claimed:
                return 0

            result = await self._deliver_claimed(claimed)
            sent = int(result["sent"])
            if result["retryable"]:
                return sent

            # A successful probe means the API is reachable. Drain in priority order,
            # bypassing per-message retry times for this recovery pass.
            sent += await self._drain_ready_messages(force=True)
            return sent

    async def on_connection_restored(self) -> int:
        async with self._send_lock:
            await self._recover_and_discard_expired()
            if await self.repository.has_sending_whatsapp_message():
                return 0
            return await self._drain_ready_messages(force=True)

    async def _drain_ready_messages(self, *, force: bool = False) -> int:
        sent = 0
        while True:
            await self._discard_expired_messages()
            if await self.repository.has_sending_whatsapp_message():
                break
            candidate, _ = await self._next_pending_message(datetime.now(UTC))
            if not candidate:
                break
            if not force and _is_future(candidate.get("next_attempt_at")):
                break

            claimed = await self.repository.claim_whatsapp_message(
                str(candidate["id"]),
                ignore_retry_time=force,
            )
            if not claimed:
                break

            result = await self._deliver_claimed(claimed)
            sent += int(result["sent"])
            if result["retryable"]:
                break
            force = True
        return sent

    async def _next_pending_message(
        self,
        now: datetime,
    ) -> tuple[dict[str, Any] | None, WhatsAppOutboxLevel | None]:
        previous_max_age = 0
        for level in self.levels:
            if level.order == "discard":
                break

            newer_than = now - timedelta(seconds=level.max_age_seconds or 0)
            older_than = (
                now - timedelta(seconds=previous_max_age)
                if previous_max_age > 0
                else None
            )
            row = await self.repository.get_pending_whatsapp_message(
                newer_than=newer_than,
                older_than=older_than,
                newest_first=level.order == "newest",
            )
            if row:
                return row, level
            previous_max_age = level.max_age_seconds or previous_max_age
        return None, None

    async def _recover_and_discard_expired(self) -> None:
        lease_seconds = max(
            self.poll_interval_seconds * 3,
            int(self.request_timeout_seconds) + 10,
        )
        await self.repository.recover_stale_whatsapp_messages(
            stale_before=datetime.now(UTC) - timedelta(seconds=lease_seconds),
        )
        await self._discard_expired_messages()

    async def _discard_expired_messages(self) -> None:
        final_retry_level = self.levels[-2]
        cutoff = datetime.now(UTC) - timedelta(
            seconds=final_retry_level.max_age_seconds or 0
        )
        await self.repository.discard_expired_whatsapp_messages(before=cutoff)

    async def _align_retry_schedule(
        self,
        row: dict[str, Any],
        level: WhatsAppOutboxLevel,
    ) -> None:
        if not row.get("last_attempt_at") or not level.retry_interval_seconds:
            return
        last_attempt = _as_datetime(row["last_attempt_at"])
        expected_next_attempt = last_attempt + timedelta(
            seconds=level.retry_interval_seconds
        )
        if row.get("next_attempt_at"):
            try:
                if _as_datetime(row["next_attempt_at"]) == expected_next_attempt:
                    return
            except (TypeError, ValueError):
                pass
        await self.repository.schedule_whatsapp_retry(
            str(row["id"]),
            retry_at=expected_next_attempt,
        )
        row["next_attempt_at"] = expected_next_attempt.isoformat()

    def _level_for_message(self, row: dict[str, Any]) -> WhatsAppOutboxLevel:
        created_at = _as_datetime(row.get("created_at"))
        age_seconds = max(0, (datetime.now(UTC) - created_at).total_seconds())
        for level in self.levels:
            if level.max_age_seconds is None or age_seconds <= level.max_age_seconds:
                return level
        return self.levels[-1]

    async def _deliver_claimed(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.send_payload(row["payload"])
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, WhatsAppClientError) and not exc.retryable:
                await self.repository.discard_whatsapp_message(
                    str(row["id"]),
                    reason="WhatsApp API rejected the message permanently",
                    error=str(exc),
                )
                retryable = False
            else:
                level = self._level_for_message(row)
                if level.order == "discard" or level.retry_interval_seconds is None:
                    await self.repository.discard_whatsapp_message(
                        str(row["id"]),
                        reason="Message exceeded the configured maximum age",
                        error=str(exc),
                    )
                    retryable = False
                else:
                    retry_at = datetime.now(UTC) + timedelta(
                        seconds=level.retry_interval_seconds
                    )
                    await self.repository.fail_whatsapp_message(
                        str(row["id"]),
                        error=str(exc),
                        retry_at=retry_at,
                    )
                    retryable = True

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
        scheduled = _as_datetime(value)
    except (TypeError, ValueError):
        return False
    return scheduled > datetime.now(UTC)


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
