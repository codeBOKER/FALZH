from typing import Any, Protocol

import httpx

from app.config import Settings
from app.utils.whatsapp_format import to_whatsapp_markup


class WhatsAppClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = (
            status_code is None
            or status_code in {408, 429}
            or status_code >= 500
        )


class WhatsAppSender(Protocol):
    async def send_text(self, remoteJid: str, text: str) -> dict[str, Any]:
        ...

    async def send_interactive_list(
        self,
        remoteJid: str,
        interactive: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class WhatsAppClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def _messages_url(self) -> str:
        base = self._settings.whatsapp_graph_url.rstrip("/")
        version = self._settings.whatsapp_api_version.strip("/")
        phone_number_id = self._settings.whatsapp_phone_number_id
        return f"{base}/{version}/{phone_number_id}/messages"

    def build_text_payload(self, remoteJid: str, text: str) -> dict[str, Any]:
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": remoteJid,
            "type": "text",
            "text": {"preview_url": False, "body": to_whatsapp_markup(text)},
        }

    def build_interactive_list_payload(
        self,
        remoteJid: str,
        interactive: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": remoteJid,
            "type": "interactive",
            "interactive": interactive,
        }

    async def send_text(self, remoteJid: str, text: str) -> dict[str, Any]:
        return await self.send_payload(self.build_text_payload(remoteJid, text))

    async def send_interactive_list(
        self,
        remoteJid: str,
        interactive: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.send_payload(
            self.build_interactive_list_payload(remoteJid, interactive)
        )

    async def send_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._settings.whatsapp_access_token}",
            "Content-Type": "application/json",
        }

        if self._client is not None:
            response = await self._client.post(
                self._messages_url,
                json=payload,
                headers=headers,
            )
        else:
            async with httpx.AsyncClient(timeout=self._settings.request_timeout_seconds) as client:
                response = await client.post(self._messages_url, json=payload, headers=headers)

        if response.status_code >= 400:
            raise WhatsAppClientError(
                f"WhatsApp API failed with {response.status_code}: {response.text}",
                status_code=response.status_code,
            )

        return response.json()
