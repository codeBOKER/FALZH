import httpx

from app.config import Settings


class WhatsAppClientError(RuntimeError):
    pass


class WhatsAppClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def _messages_url(self) -> str:
        base = self._settings.whatsapp_graph_url.rstrip("/")
        version = self._settings.whatsapp_api_version.strip("/")
        phone_number_id = self._settings.whatsapp_phone_number_id
        print(base)
        return f"{base}/{version}/{phone_number_id}/messages"

    async def send_text(self, remoteJid: str, text: str) -> dict:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": remoteJid,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        headers = {
            "Authorization": f"Bearer {self._settings.whatsapp_access_token}",
            "Content-Type": "application/json",
        }

        if self._client is not None:
            response = await self._client.post(self._messages_url, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._settings.request_timeout_seconds) as client:
                response = await client.post(self._messages_url, json=payload, headers=headers)

        if response.status_code >= 400:
            raise WhatsAppClientError(
                f"WhatsApp API failed with {response.status_code}: {response.text}"
            )

        return response.json()

    async def send_interactive_list(
        self,
        remoteJid: str,
        interactive: dict[str, Any],
    ) -> dict:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": remoteJid,
            "type": "interactive",
            "interactive": interactive,
        }
        headers = {
            "Authorization": f"Bearer {self._settings.whatsapp_access_token}",
            "Content-Type": "application/json",
        }

        if self._client is not None:
            response = await self._client.post(self._messages_url, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._settings.request_timeout_seconds) as client:
                response = await client.post(self._messages_url, json=payload, headers=headers)

        if response.status_code >= 400:
            raise WhatsAppClientError(
                f"WhatsApp API failed with {response.status_code}: {response.text}"
            )

        return response.json()
