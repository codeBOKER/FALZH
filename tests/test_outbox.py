import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import WhatsAppOutboxLevel
from app.whatsapp.client import WhatsAppClientError
from app.whatsapp.outbox import WhatsAppOutbox

LEVELS = [
    WhatsAppOutboxLevel(max_age_seconds=120, retry_interval_seconds=10, order="oldest"),
    WhatsAppOutboxLevel(max_age_seconds=36_000, retry_interval_seconds=60, order="newest"),
    WhatsAppOutboxLevel(max_age_seconds=None, retry_interval_seconds=None, order="discard"),
]


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


class FakeOutboxRepository:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.seq = 0
        self.sent_marks: list[str] = []

    async def enqueue_whatsapp_messages(
        self,
        *,
        recipient: str,
        payloads: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        base = datetime.now(UTC)
        created: list[dict[str, Any]] = []
        for index, payload in enumerate(payloads):
            self.seq += 1
            row = {
                "id": f"out-{self.seq:04d}",
                "recipient": recipient,
                "payload": payload,
                "status": "pending",
                "attempts": 0,
                "next_attempt_at": _iso(base + timedelta(microseconds=index)),
                "created_at": _iso(base + timedelta(microseconds=index)),
                "last_attempt_at": None,
                "locked_at": None,
                "last_error": None,
            }
            self.rows[row["id"]] = row
            created.append(dict(row))
        return created

    async def has_pending_whatsapp_messages(self, recipient: str) -> bool:
        return any(
            row["recipient"] == recipient and row["status"] in ("pending", "sending")
            for row in self.rows.values()
        )

    async def get_pending_whatsapp_message(
        self,
        *,
        recipient: str,
        newer_than: datetime | None,
        older_than: datetime | None,
        newest_first: bool,
    ) -> dict[str, Any] | None:
        candidates = [
            row
            for row in self.rows.values()
            if row["recipient"] == recipient and row["status"] == "pending"
        ]
        if newer_than is not None:
            candidates = [row for row in candidates if _parse(row["created_at"]) >= newer_than]
        if older_than is not None:
            candidates = [row for row in candidates if _parse(row["created_at"]) < older_than]
        if not candidates:
            return None
        candidates.sort(
            key=lambda row: (_parse(row["created_at"]), row["id"]),
            reverse=newest_first,
        )
        return dict(candidates[0])

    async def list_pending_whatsapp_queue_heads(self) -> list[dict[str, Any]]:
        heads: dict[str, dict[str, Any]] = {}
        for row in sorted(
            (row for row in self.rows.values() if row["status"] == "pending"),
            key=lambda row: (_parse(row["created_at"]), row["id"]),
        ):
            heads.setdefault(
                row["recipient"],
                {
                    "recipient": row["recipient"],
                    "created_at": row["created_at"],
                    "next_attempt_at": row["next_attempt_at"],
                },
            )
        return list(heads.values())

    async def claim_whatsapp_message(
        self,
        message_id: str,
        *,
        ignore_retry_time: bool = False,
    ) -> dict[str, Any] | None:
        row = self.rows.get(message_id)
        if row is None:
            return None
        if any(
            other["recipient"] == row["recipient"] and other["status"] == "sending"
            for other in self.rows.values()
        ):
            return None
        if row["status"] != "pending":
            return None
        if not ignore_retry_time and _parse(row["next_attempt_at"]) > datetime.now(UTC):
            return None
        row.update(
            status="sending",
            attempts=int(row["attempts"]) + 1,
            last_attempt_at=_iso(datetime.now(UTC)),
            locked_at=_iso(datetime.now(UTC)),
            last_error=None,
        )
        return dict(row)

    async def mark_whatsapp_message_sent(
        self,
        message_id: str,
        *,
        provider_response: dict[str, Any],
    ) -> None:
        self.rows[message_id].update(
            status="sent",
            sent_at=_iso(datetime.now(UTC)),
            locked_at=None,
        )
        self.sent_marks.append(message_id)

    async def fail_whatsapp_message(
        self,
        message_id: str,
        *,
        error: str,
        retry_at: datetime,
    ) -> None:
        self.rows[message_id].update(
            status="pending",
            next_attempt_at=_iso(retry_at),
            locked_at=None,
            last_error=error,
        )

    async def discard_whatsapp_message(
        self,
        message_id: str,
        *,
        reason: str,
        error: str | None = None,
    ) -> None:
        self.rows[message_id].update(
            status="discarded",
            locked_at=None,
            discard_reason=reason,
            last_error=error,
        )

    async def recover_stale_whatsapp_messages(self, *, stale_before: datetime) -> None:
        for row in self.rows.values():
            if (
                row["status"] == "sending"
                and row["locked_at"]
                and _parse(row["locked_at"]) < stale_before
            ):
                row.update(
                    status="pending",
                    next_attempt_at=_iso(datetime.now(UTC)),
                    locked_at=None,
                )

    async def discard_expired_whatsapp_messages(self, *, before: datetime) -> None:
        for row in self.rows.values():
            if row["status"] == "pending" and _parse(row["created_at"]) < before:
                row.update(status="discarded", locked_at=None)

    async def schedule_whatsapp_retry(self, message_id: str, *, retry_at: datetime) -> None:
        row = self.rows[message_id]
        if row["status"] == "pending":
            row["next_attempt_at"] = _iso(retry_at)


class FakeWhatsAppClient:
    def __init__(self) -> None:
        self.sent_payloads: list[dict[str, Any]] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.fail_on: set[int] = set()
        self.always_fail = False
        self.fail_status = 500
        self.calls = 0
        self.closed = False

    def build_text_payload(self, remoteJid: str, text: str) -> dict[str, Any]:
        return {"to": remoteJid, "type": "text", "text": {"body": text}}

    def build_interactive_list_payload(
        self,
        remoteJid: str,
        interactive: dict[str, Any],
    ) -> dict[str, Any]:
        return {"to": remoteJid, "type": "interactive", "interactive": interactive}

    async def send_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        gate = self.gates.get(payload["to"])
        if gate is not None:
            await gate.wait()
        self.calls += 1
        if self.always_fail or self.calls in self.fail_on:
            raise WhatsAppClientError("boom", status_code=self.fail_status)
        self.sent_payloads.append(payload)
        return {"messages": [{"id": f"wamid.{len(self.sent_payloads)}"}]}

    async def aclose(self) -> None:
        self.closed = True


def make_outbox(
    repository: FakeOutboxRepository | None = None,
    client: FakeWhatsAppClient | None = None,
) -> WhatsAppOutbox:
    return WhatsAppOutbox(
        repository=repository or FakeOutboxRepository(),  # type: ignore[arg-type]
        client=client or FakeWhatsAppClient(),  # type: ignore[arg-type]
        levels=LEVELS,
        request_timeout_seconds=20.0,
    )


def bodies(client: FakeWhatsAppClient) -> list[str]:
    return [payload["text"]["body"] for payload in client.sent_payloads]


async def test_send_texts_sends_directly_without_outbox_rows() -> None:
    repository = FakeOutboxRepository()
    client = FakeWhatsAppClient()
    outbox = make_outbox(repository, client)

    responses = await outbox.send_texts("chat-a", ["card-1", "card-2", "card-3"])

    assert bodies(client) == ["card-1", "card-2", "card-3"]
    assert repository.rows == {}
    for index, response in enumerate(responses, start=1):
        assert response["messages"][0]["id"] == f"wamid.{index}"
        assert response.get("queued") is not True
        assert response.get("outbox_id") is None


async def test_send_text_returns_single_response() -> None:
    repository = FakeOutboxRepository()
    client = FakeWhatsAppClient()
    outbox = make_outbox(repository, client)

    response = await outbox.send_text("chat-a", "hello")

    assert bodies(client) == ["hello"]
    assert response["messages"][0]["id"] == "wamid.1"
    assert repository.rows == {}


async def test_slow_recipient_does_not_block_another() -> None:
    client = FakeWhatsAppClient()
    blocked = asyncio.Event()
    client.gates["chat-a"] = blocked
    outbox = make_outbox(client=client)

    slow_send = asyncio.create_task(outbox.send_texts("chat-a", ["a-1", "a-2"]))
    await asyncio.sleep(0)
    fast_responses = await outbox.send_texts("chat-b", ["b-1"])

    assert bodies(client) == ["b-1"]
    assert fast_responses[0]["messages"][0]["id"] == "wamid.1"

    blocked.set()
    await slow_send
    assert bodies(client) == ["b-1", "a-1", "a-2"]


async def test_retryable_failure_is_queued_then_drained() -> None:
    repository = FakeOutboxRepository()
    client = FakeWhatsAppClient()
    client.fail_on = {1}
    outbox = make_outbox(repository, client)

    response = await outbox.send_text("chat-a", "failing")

    assert response.get("queued") is True
    assert response["outbox_id"]
    assert client.sent_payloads == []
    assert repository.rows[response["outbox_id"]]["status"] == "pending"

    row = repository.rows[response["outbox_id"]]
    row["next_attempt_at"] = _iso(datetime.now(UTC) - timedelta(seconds=1))

    sent = await outbox.retry_pending_messages()

    assert sent == 1
    assert bodies(client) == ["failing"]


async def test_partial_batch_keeps_order_and_queues_the_rest() -> None:
    repository = FakeOutboxRepository()
    client = FakeWhatsAppClient()
    client.fail_on = {2}
    outbox = make_outbox(repository, client)

    responses = await outbox.send_texts("chat-a", ["c1", "c2", "c3"])

    assert bodies(client) == ["c1"]
    assert responses[0]["messages"][0]["id"] == "wamid.1"
    assert responses[1].get("queued") is True
    assert responses[2].get("queued") is True
    queued = [row for row in repository.rows.values() if row["status"] == "pending"]
    assert [row["payload"]["text"]["body"] for row in queued] == ["c2", "c3"]


async def test_permanent_failure_is_queued_not_dropped() -> None:
    repository = FakeOutboxRepository()
    client = FakeWhatsAppClient()
    client.fail_on = {1}
    client.fail_status = 400
    outbox = make_outbox(repository, client)

    response = await outbox.send_text("chat-a", "bad")

    assert response.get("failed") is not True
    assert response.get("queued") is True
    assert response["outbox_id"]
    assert repository.rows[response["outbox_id"]]["status"] == "pending"
    assert client.sent_payloads == []


async def test_permanent_failure_is_discarded_with_a_reason_after_a_retry() -> None:
    repository = FakeOutboxRepository()
    client = FakeWhatsAppClient()
    client.always_fail = True
    client.fail_status = 400
    outbox = make_outbox(repository, client)

    response = await outbox.send_text("chat-a", "bad")
    row = repository.rows[response["outbox_id"]]
    row["next_attempt_at"] = _iso(datetime.now(UTC) - timedelta(seconds=1))

    sent = await outbox.retry_pending_messages()

    assert sent == 0
    assert row["status"] == "discarded"
    assert "rejected" in row["discard_reason"]


async def test_new_send_queues_behind_existing_backlog() -> None:
    repository = FakeOutboxRepository()
    client = FakeWhatsAppClient()
    outbox = make_outbox(repository, client)

    rows = await repository.enqueue_whatsapp_messages(
        recipient="chat-a",
        payloads=[client.build_text_payload("chat-a", "old")],
    )
    repository.rows[rows[0]["id"]]["next_attempt_at"] = _iso(
        datetime.now(UTC) + timedelta(seconds=30)
    )

    response = await outbox.send_text("chat-a", "new")

    assert response.get("queued") is True
    assert response["outbox_id"] != rows[0]["id"]
    assert client.sent_payloads == []
    assert repository.rows[rows[0]["id"]]["status"] == "pending"

    queued = sorted(repository.rows.values(), key=lambda row: row["created_at"])
    assert [row["payload"]["text"]["body"] for row in queued] == ["old", "new"]


async def test_batch_queues_behind_existing_backlog_in_order() -> None:
    repository = FakeOutboxRepository()
    client = FakeWhatsAppClient()
    outbox = make_outbox(repository, client)

    rows = await repository.enqueue_whatsapp_messages(
        recipient="chat-a",
        payloads=[client.build_text_payload("chat-a", "old")],
    )
    repository.rows[rows[0]["id"]]["next_attempt_at"] = _iso(
        datetime.now(UTC) + timedelta(seconds=30)
    )

    responses = await outbox.send_texts("chat-a", ["new-1", "new-2"])

    assert [response.get("queued") for response in responses] == [True, True]
    assert client.sent_payloads == []
    queued = sorted(repository.rows.values(), key=lambda row: row["created_at"])
    assert [row["payload"]["text"]["body"] for row in queued] == ["old", "new-1", "new-2"]


async def test_flush_pending_drains_every_recipient() -> None:
    repository = FakeOutboxRepository()
    client = FakeWhatsAppClient()
    outbox = make_outbox(repository, client)

    rows_a = await repository.enqueue_whatsapp_messages(
        recipient="chat-a",
        payloads=[client.build_text_payload("chat-a", "a-1")],
    )
    rows_b = await repository.enqueue_whatsapp_messages(
        recipient="chat-b",
        payloads=[client.build_text_payload("chat-b", "b-1")],
    )
    for row in (*rows_a, *rows_b):
        repository.rows[row["id"]]["next_attempt_at"] = _iso(
            datetime.now(UTC) + timedelta(seconds=30)
        )

    sent = await outbox.flush_pending()

    assert sent == 2
    assert sorted(bodies(client)) == ["a-1", "b-1"]


async def test_retry_worker_drains_backlog_across_recipients() -> None:
    repository = FakeOutboxRepository()
    client = FakeWhatsAppClient()
    outbox = make_outbox(repository, client)

    await repository.enqueue_whatsapp_messages(
        recipient="chat-a",
        payloads=[client.build_text_payload("chat-a", "a-1")],
    )
    await repository.enqueue_whatsapp_messages(
        recipient="chat-b",
        payloads=[client.build_text_payload("chat-b", "b-1")],
    )

    sent = await outbox.retry_pending_messages()

    assert sent == 2
    assert sorted(bodies(client)) == ["a-1", "b-1"]


async def test_connection_restored_force_drains_retry_backoff() -> None:
    repository = FakeOutboxRepository()
    client = FakeWhatsAppClient()
    outbox = make_outbox(repository, client)

    rows = await repository.enqueue_whatsapp_messages(
        recipient="chat-a",
        payloads=[client.build_text_payload("chat-a", "old-1")],
    )
    repository.rows[rows[0]["id"]]["next_attempt_at"] = _iso(
        datetime.now(UTC) + timedelta(seconds=30)
    )

    sent = await outbox.on_connection_restored()

    assert sent == 1
    assert bodies(client) == ["old-1"]


async def test_stale_sending_row_recovers_without_blocking_other_recipients() -> None:
    repository = FakeOutboxRepository()
    client = FakeWhatsAppClient()
    outbox = make_outbox(repository, client)

    stuck = await repository.enqueue_whatsapp_messages(
        recipient="chat-stuck",
        payloads=[client.build_text_payload("chat-stuck", "stuck")],
    )
    repository.rows[stuck[0]["id"]].update(
        status="sending",
        locked_at=_iso(datetime.now(UTC) - timedelta(minutes=10)),
    )

    responses = await outbox.send_texts("chat-free", ["free"])

    assert bodies(client) == ["free"]
    assert responses[0]["messages"][0]["id"] == "wamid.1"

    sent = await outbox.retry_pending_messages()
    assert sent == 1
    assert bodies(client) == ["free", "stuck"]
