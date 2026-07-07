from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.domain import ToolResult
from tests.conftest import signed_body, whatsapp_payload


def test_whatsapp_verification_success(test_app):
    with TestClient(test_app) as client:
        response = client.get(
            "/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-token",
                "hub.challenge": "challenge-code",
            },
        )

    assert response.status_code == 200
    assert response.text == "challenge-code"


def test_whatsapp_verification_rejects_wrong_token(test_app):
    with TestClient(test_app) as client:
        response = client.get(
            "/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "challenge-code",
            },
        )

    assert response.status_code == 401


def test_post_webhook_validates_signature_and_accepts_text(test_app):
    payload = whatsapp_payload(message_id="wamid.100", text="I need Aden to Mukalla")
    body, signature = signed_body(payload, "app-secret")

    with TestClient(test_app) as client:
        response = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={
                "content-type": "application/json",
                "x-hub-signature-256": signature,
            },
        )

    conversation = test_app.state.test_container.conversation
    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "messages": 1}
    assert len(conversation.calls) == 1
    assert conversation.calls[0].text == "I need Aden to Mukalla"


def test_post_webhook_debug_returns_reply_and_status(test_app):
    payload = whatsapp_payload(message_id="wamid.200", text="Debug this message")
    body, signature = signed_body(payload, "app-secret")

    with TestClient(test_app) as client:
        response = client.post(
            "/webhooks/whatsapp/debug",
            content=body,
            headers={
                "content-type": "application/json",
                "x-hub-signature-256": signature,
            },
        )

    conversation = test_app.state.test_container.conversation
    assert response.status_code == 200
    assert response.json() == {
        "status": "accepted",
        "messages": 1,
        "replies": ["ok"],
    }
    assert len(conversation.calls) == 1


def test_post_webhook_rejects_bad_signature(test_app):
    payload = whatsapp_payload()
    body, _ = signed_body(payload, "app-secret")

    with TestClient(test_app) as client:
        response = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={
                "content-type": "application/json",
                "x-hub-signature-256": "sha256=bad",
            },
        )

    assert response.status_code == 401


def test_admin_routes_require_key(test_app):
    with TestClient(test_app) as client:
        rejected = client.post("/admin/seed-info")
        accepted = client.post("/admin/sync-trips", headers={"X-Admin-Api-Key": "admin-secret"})

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json() == {"indexed_trips": 3}


def test_admin_driver_debug_returns_llm_and_tool_results(settings):
    class DummyPrimaryProvider:
        async def chat(self, messages, tools, tool_choice, temperature):
            return SimpleNamespace(
                content="Driver debug reply",
                tool_calls=[
                    SimpleNamespace(
                        id="call-1",
                        name="add_trip_by_driver",
                        arguments='{"departure":"A","destination":"B","departure_date":"2026-06-20","departure_time":"morning","available_seats":2,"total_seats":4,"price":30}',
                    )
                ],
            )

    class DummyConversation:
        def _tool_registry(self, customer, *, remoteJid, user_mode="driver", current_message=None):
            class DummyRegistry:
                async def execute(self, name, arguments):
                    return ToolResult(ok=True, data={"name": name, "arguments": arguments})

            return DummyRegistry()

    container = SimpleNamespace(
        settings=settings,
        conversation=DummyConversation(),
        ai=SimpleNamespace(primary=DummyPrimaryProvider()),
    )
    app = create_app(settings=settings, container=container)

    with TestClient(app) as client:
        response = client.post(
            "/admin/driver-debug",
            json={"message": "debug driver", "client_number": "967700000002"},
            headers={"X-Admin-Api-Key": "admin-secret"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_response"] == "Driver debug reply"
    assert payload["tool_calls"][0]["name"] == "add_trip_by_driver"
    assert payload["tool_results"][0]["result"]["ok"] is True
