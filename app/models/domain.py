from dataclasses import dataclass, field
from typing import Any, Literal

SenderType = Literal["customer", "assistant", "driver", "system"]
UserMode = Literal["new_user", "driver", "passenger"]


@dataclass(slots=True)
class WhatsAppInboundMessage:
    message_id: str
    remoteJid: str
    text: str
    timestamp: str | None = None
    profile_name: str | None = None
    phone_number_id: str | None = None
    message_type: str = "text"
    interactive_reply_id: str | None = None
    context_message_id: str | None = None
    phone_number: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(slots=True)
class AIProviderResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResult:
    ok: bool
    data: dict[str, Any]
    error: str | None = None
    suppress_llm_reply: bool = False

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok, "data": self.data}
        if self.error:
            payload["error"] = self.error
        return payload
