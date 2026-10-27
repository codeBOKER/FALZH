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
        self._recipient_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, recipient: str) -> asyncio.Lock:
        lock = self._recipient_locks.get(recipient)
        if lock is None:
            lock = asyncio.Lock()
            self._recipient_locks[recipient] = lock
        return lock

    async def send_text(self, remoteJid: str, text: str) -> dict[str, Any]:
        return (await self.send_texts(remoteJid, [text]))[0]

    async def send_texts(self, remoteJid: str, texts: list[str]) -> list[dict[str, Any]]:
        if not texts:
            return []
        payloads = [self.client.build_text_payload(remoteJid, text) for text in texts]
        return await self._send_payloads(remoteJid, payloads)

    async def send_interactive_list(
        self,
        remoteJid: str,
        interactive: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self.client.build_interactive_list_payload(remoteJid, interactive)
        return (await self._send_payloads(remoteJid, [payload]))[0]

    async def _send_payloads(
        self,
        recipient: str,
        payloads: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # Per-recipient lock keeps messages for the same chat ordered while
        # letting different chats send concurrently.
        async with self._lock_for(recipient):
            if await self.repository.has_pending_whatsapp_messages(recipient):
                # This chat already has a backlog, so queue behind it instead of
                # sending directly: a new message must never overtake an older
                # one that is still waiting to be delivered.
                return await self._queue_payloads(recipient, payloads)

            responses: list[dict[str, Any]] = []
            queue_from: int | None = None
            for index, payload in enumerate(payloads):
                try:
                    responses.append(await self.client.send_payload(payload))
                except Exception as exc:  # noqa: BLE001
                    # Queue this message and every message after it so delivery
                    # order is preserved. Even non-retryable rejections (e.g. 4xx)
                    # must be persisted rather than silently dropped.
                    if isinstance(exc, WhatsAppClientError) and not exc.retryable:
                        logger.warning(
                            "WhatsApp rejected a message for %s permanently: %s",
                            recipient,
                            exc,
                        )
                    queue_from = index
                    break

            if queue_from is not None:
                responses.extend(
                    await self._queue_payloads(recipient, payloads[queue_from:])
                )
            return responses

    async def _queue_payloads(
        self,
        recipient: str,
        payloads: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows = await self.repository.enqueue_whatsapp_messages(
            recipient=recipient,
            payloads=payloads,
        )
        return [_queued_response(row["id"]) for row in rows]

    async def run_retry_worker(self) -> None:
        while True:
            await asyncio.sleep(self.poll_interval_seconds)
            try:
                await self.retry_pending_messages()
            except Exception:
                logger.exception("WhatsApp outbox retry pass failed")

    async def retry_pending_messages(self) -> int:
        await self._run_maintenance()
        sent = 0
        for head in await self.repository.list_pending_whatsapp_queue_heads():
            recipient = str(head["recipient"])
            async with self._lock_for(recipient):
                delivered = await self._drain_recipient(recipient, force=False)
                sent += sum(1 for response in delivered.values() if response.get("sent"))
        return sent

    async def on_connection_restored(self) -> int:
        await self._run_maintenance()
        sent = 0
        for head in await self.repository.list_pending_whatsapp_queue_heads():
            recipient = str(head["recipient"])
            async with self._lock_for(recipient):
                delivered = await self._drain_recipient(recipient, force=True)
                sent += sum(1 for response in delivered.values() if response.get("sent"))
        return sent

    async def flush_pending(self) -> int:
        """Force-deliver every queued message for every recipient.

        Called on inbound webhooks so a freshly reached chat flushes its
        pending backlog immediately. Skips maintenance: recovery belongs to
        the retry worker and the connection-restored path.
        """
        sent = 0
        for head in await self.repository.list_pending_whatsapp_queue_heads():
            recipient = str(head["recipient"])
            async with self._lock_for(recipient):
                delivered = await self._drain_recipient(recipient, force=True)
                sent += sum(1 for response in delivered.values() if response.get("sent"))
        return sent

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _drain_recipient(
        self,
        recipient: str,
        *,
        force: bool,
    ) -> dict[str, dict[str, Any]]:
        delivered: dict[str, dict[str, Any]] = {}
        while True:
            now = datetime.now(UTC)
            candidate, level = await self._next_pending_message(recipient, now)
            if not candidate or level is None:
                break
            if not force:
                await self._align_retry_schedule(candidate, level)
                if _is_future(candidate.get("next_attempt_at")):
                    break

            claimed = await self.repository.claim_whatsapp_message(
                str(candidate["id"]),
                ignore_retry_time=force,
            )
            if not claimed:
                break

            result = await self._deliver_claimed(claimed)
            delivered[str(claimed["id"])] = result.get("response") or _queued_response(
                claimed.get("id")
            )
            if result["retryable"]:
                break
            # A successful delivery means the API is reachable; keep draining
            # the rest of this recipient's queue without waiting out retries.
            force = True
        return delivered

    async def _next_pending_message(
        self,
        recipient: str,
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
                recipient=recipient,
                newer_than=newer_than,
                older_than=older_than,
                newest_first=level.order == "newest",
            )
            if row:
                return row, level
            previous_max_age = level.max_age_seconds or previous_max_age
        return None, None

    async def _run_maintenance(self) -> None:
        lease_seconds = max(
            self.poll_interval_seconds * 3,
            int(self.request_timeout_seconds) + 10,
        )
        await self.repository.recover_stale_whatsapp_messages(
            stale_before=datetime.now(UTC) - timedelta(seconds=lease_seconds),
        )
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
                "response": _queued_response(
                    row.get("id"),
                    error=str(exc),
                    retryable=retryable,
                ),
            }

        await self.repository.mark_whatsapp_message_sent(
            str(row["id"]),
            provider_response=response,
        )
        return {
            "sent": True,
            "retryable": False,
            "response": {
                **response,
                "outbox_id": row.get("id"),
                "sent": True,
            },
        }


def _queued_response(
    outbox_id: Any,
    *,
    error: str | None = None,
    retryable: bool = True,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "messages": [{}],
        "queued": retryable,
        "failed": not retryable,
        "outbox_id": outbox_id,
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
