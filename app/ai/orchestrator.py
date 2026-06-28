import json
import logging
from typing import Any

from app.ai.providers import (
    ChatProvider,
    InvalidToolCallGenerationError,
    ProviderError,
    RetryableProviderError,
)
from app.models.domain import AIProviderResponse, ToolCall
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class AIOrchestrator:
    def __init__(
        self,
        *,
        primary: ChatProvider,
        fallback: ChatProvider,
        temperature: float,
        max_tool_iterations: int,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.temperature = temperature
        self.max_tool_iterations = max_tool_iterations

    async def generate_reply(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        registry: ToolRegistry,
    ) -> str:
        try:
            return await self._run_provider(
                self.primary,
                messages=messages,
                tools=tools,
                registry=registry,
                temperature=self.temperature,
            )
        except InvalidToolCallGenerationError:
            logger.warning("Primary provider generated invalid tool call; retrying once")
            try:
                return await self._run_provider(
                    self.primary,
                    messages=messages,
                    tools=tools,
                    registry=registry,
                    temperature=max(self.temperature - 0.2, 0.1),
                )
            except RetryableProviderError:
                logger.warning("Primary retry failed; falling back to Hugging Face")
        except RetryableProviderError:
            logger.warning("Primary provider failed; falling back to Hugging Face")

        return await self._run_provider(
            self.fallback,
            messages=messages,
            tools=tools,
            registry=registry,
            temperature=self.temperature,
        )

    async def _run_provider(
        self,
        provider: ChatProvider,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        registry: ToolRegistry,
        temperature: float,
    ) -> str:
        working_messages = [dict(message) for message in messages]
        # need to be edited to reponse quackly instead in enter in a for loop
        for _ in range(self.max_tool_iterations + 1):
            response = await provider.chat(
                working_messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
            )
            if not response.tool_calls:
                content = (response.content or "").strip()
                if content:
                    return content
                raise ProviderError(f"{provider.name} returned an empty response")
            working_messages.append(_assistant_tool_message(response))
            for tool_call in response.tool_calls:
                logger.warning("++++++++"+str(tool_call)+ "&&&"+ str(registry))
                result = await _execute_tool_call(registry, tool_call)
                logger.warning("+++++++++++++"+str(result))
                working_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        return (
            "I found that this request needs extra checking. "
            "A support team member will follow up with you shortly."
        )


def _assistant_tool_message(response: AIProviderResponse) -> dict[str, Any]:
    if response.raw_message:
        return response.raw_message
    return {
        "role": "assistant",
        "content": response.content,
        "tool_calls": [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {"name": tool_call.name, "arguments": tool_call.arguments},
            }
            for tool_call in response.tool_calls
        ],
    }


async def _execute_tool_call(registry: ToolRegistry, tool_call: ToolCall) -> dict[str, Any]:
    try:
        arguments = json.loads(tool_call.arguments or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "error": f"Invalid tool arguments: {exc}", "data": {}}

    return (await registry.execute(tool_call.name, arguments)).to_payload()
