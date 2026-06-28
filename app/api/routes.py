from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)

from app.api.deps import get_container, verify_admin_api_key
from app.models.api import (
    HealthResponse,
    DriverDebugRequest,
    LLMToolCallRequest,
    LLMToolCallResponse,
    JinaEmbeddingRequest,
    JinaEmbeddingResponse,
    SeedInfoResponse,
    SyncTripsResponse,
    WebhookAcceptedResponse,
    WebhookDebugResponse,
)
from app.services.container import ServiceContainer
from app.whatsapp.parser import parse_inbound_messages
from app.whatsapp.security import verify_meta_signature, verify_webhook_challenge
from app.ai.tool_schemas import get_all_tool_schemas
import json

router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
async def healthz(container: Annotated[ServiceContainer, Depends(get_container)]) -> HealthResponse:
    return HealthResponse(service=container.settings.app_name)


@router.get("/webhooks/whatsapp")
async def verify_whatsapp_webhook(
    container: Annotated[ServiceContainer, Depends(get_container)],
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> Response:
    if verify_webhook_challenge(
        hub_mode,
        hub_verify_token,
        container.settings.whatsapp_verify_token,
    ):
        return Response(content=hub_challenge or "", media_type="text/plain")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid verify token")


@router.post("/webhooks/whatsapp", response_model=WebhookAcceptedResponse)
async def receive_whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    container: Annotated[ServiceContainer, Depends(get_container)],
) -> WebhookAcceptedResponse:
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    if not verify_meta_signature(body, signature, container.settings.whatsapp_app_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    payload: dict[str, Any] = await request.json()
    messages = parse_inbound_messages(payload)
    for inbound in messages:
        background_tasks.add_task(container.conversation.handle_inbound_message, inbound)

    return WebhookAcceptedResponse(messages=len(messages))


@router.post(
    "/webhooks/whatsapp/debug",
    response_model=WebhookDebugResponse,
)
async def receive_whatsapp_webhook_debug(
    request: Request,
    container: Annotated[ServiceContainer, Depends(get_container)],
) -> WebhookDebugResponse:
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    # if not verify_meta_signature(body, signature, container.settings.whatsapp_app_secret):
    #     raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    payload: dict[str, Any] = await request.json()
    messages = parse_inbound_messages(payload)
    replies: list[str] = []
    for inbound in messages:
        reply = await container.conversation.handle_inbound_message(inbound)
        if reply is not None:
            replies.append(reply)

    return WebhookDebugResponse(messages=len(messages), replies=replies)


@router.post(
    "/admin/jina-embed",
    response_model=JinaEmbeddingResponse,
    dependencies=[Depends(verify_admin_api_key)],
)
async def jina_embed_query(
    request: JinaEmbeddingRequest,
    container: Annotated[ServiceContainer, Depends(get_container)],
) -> JinaEmbeddingResponse:
    embedding = await container.embeddings.embed_query(request.text)
    return JinaEmbeddingResponse(
        text=request.text,
        embedding=embedding,
        dimensions=len(embedding),
    )


@router.post(
    "/admin/llm-tool-call",
    response_model=LLMToolCallResponse,
    dependencies=[Depends(verify_admin_api_key)],
)
async def llm_tool_call_debug(
    request: LLMToolCallRequest,
    container: Annotated[ServiceContainer, Depends(get_container)],
) -> LLMToolCallResponse:
    # Call the primary provider directly with the tool schemas and return generated tool calls
    provider = container.ai.primary
    tools = get_all_tool_schemas()
    response = await provider.chat(
        messages=[{"role": "user", "content": request.message}],
        tools=tools,
        tool_choice="auto",
        temperature=container.settings.ai_temperature,
    )

    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    registry = container.conversation._tool_registry(
        {"id": "admin-debug", "remoteJid": "", "name": "admin-debug"},
        remoteJid="admin-debug",
        user_mode="passenger",
    )

    for tc in response.tool_calls:
        try:
            args = json.loads(tc.arguments or "{}")
        except Exception:
            args = tc.arguments
        tool_calls.append({"name": tc.name, "arguments": args})

        execution_result = await registry.execute(tc.name, args if isinstance(args, dict) else {})
        tool_results.append(
            {
                "tool_call_id": tc.id,
                "name": tc.name,
                "arguments": args,
                "result": execution_result.to_payload(),
            }
        )

    return LLMToolCallResponse(
        llm_response=(response.content or "").strip() or None,
        tool_calls=tool_calls,
        tool_results=tool_results,
    )


@router.post(
    "/admin/driver-debug",
    response_model=LLMToolCallResponse,
    dependencies=[Depends(verify_admin_api_key)],
)
async def driver_service_debug(
    request: DriverDebugRequest,
    container: Annotated[ServiceContainer, Depends(get_container)],
) -> LLMToolCallResponse:
    provider = container.ai.primary
    tools = get_all_tool_schemas()
    response = await provider.chat(
        messages=[{"role": "user", "content": request.message}],
        tools=tools,
        tool_choice="auto",
        temperature=container.settings.ai_temperature,
    )

    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    customer = {
        "id": "debug",
        "remoteJid": "823904580238",
        "name": "driver-debug",
    }
    registry = container.conversation._tool_registry(
        customer,
        remoteJid=request.client_number,
        user_mode="driver",
    )

    for tc in response.tool_calls:
        try:
            args = json.loads(tc.arguments or "{}")
        except Exception:
            args = tc.arguments
        tool_calls.append({"name": tc.name, "arguments": args})

        execution_result = await registry.execute(tc.name, args if isinstance(args, dict) else {})
        tool_results.append(
            {
                "tool_call_id": tc.id,
                "name": tc.name,
                "arguments": args,
                "result": execution_result.to_payload(),
            }
        )

    return LLMToolCallResponse(
        llm_response=(response.content or "").strip() or None,
        tool_calls=tool_calls,
        tool_results=tool_results,
    )


@router.post(
    "/admin/seed-info",
    response_model=SeedInfoResponse,
    dependencies=[Depends(verify_admin_api_key)],
)
async def seed_info(
    container: Annotated[ServiceContainer, Depends(get_container)],
) -> SeedInfoResponse:
    return SeedInfoResponse(indexed_chunks=await container.admin.seed_info())


@router.post(
    "/admin/sync-trips",
    response_model=SyncTripsResponse,
    dependencies=[Depends(verify_admin_api_key)],
)
async def sync_trips(
    container: Annotated[ServiceContainer, Depends(get_container)],
) -> SyncTripsResponse:
    return SyncTripsResponse(indexed_trips=await container.admin.sync_trips())


