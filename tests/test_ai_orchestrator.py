import pytest

from app.ai.orchestrator import AIOrchestrator
from app.ai.providers import InvalidToolCallGenerationError, RetryableProviderError
from app.models.domain import AIProviderResponse, ToolCall
from app.tools.registry import ToolRegistry
from tests.conftest import ok_tool


class ScriptedProvider:
    def __init__(self, name, script):
        self.name = name
        self.script = list(script)
        self.calls = []

    async def chat(self, messages, *, tools=None, tool_choice="auto", temperature=0.2):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "temperature": temperature,
            }
        )
        next_item = self.script.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


@pytest.mark.asyncio
async def test_ai_falls_back_when_primary_rate_limited():
    primary = ScriptedProvider("groq", [RetryableProviderError("rate limited")])
    fallback = ScriptedProvider("gemini", [AIProviderResponse(content="fallback reply")])
    registry = ToolRegistry()
    orchestrator = AIOrchestrator(
        primary=primary,
        fallback=fallback,
        temperature=0.2,
        max_tool_iterations=3,
    )

    reply = await orchestrator.generate_reply(messages=[], tools=[], registry=registry)

    assert reply == "fallback reply"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_ai_retries_invalid_groq_tool_generation_before_fallback():
    primary = ScriptedProvider(
        "groq",
        [
            InvalidToolCallGenerationError("bad tool"),
            AIProviderResponse(content="primary retry reply"),
        ],
    )
    fallback = ScriptedProvider("gemini", [AIProviderResponse(content="fallback")])
    orchestrator = AIOrchestrator(
        primary=primary,
        fallback=fallback,
        temperature=0.4,
        max_tool_iterations=3,
    )

    reply = await orchestrator.generate_reply(messages=[], tools=[], registry=ToolRegistry())

    assert reply == "primary retry reply"
    assert [call["temperature"] for call in primary.calls] == [0.4, 0.2]
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_ai_executes_tool_call_and_returns_final_response():
    primary = ScriptedProvider(
        "groq",
        [
            AIProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="about_falzh",
                        arguments='{"query":"FALZH","language":"en"}',
                    )
                ]
            ),
            AIProviderResponse(content="FALZH helps with travel booking."),
        ],
    )
    registry = ToolRegistry()
    registry.register("about_falzh", ok_tool)
    orchestrator = AIOrchestrator(
        primary=primary,
        fallback=ScriptedProvider("hf", []),
        temperature=0.2,
        max_tool_iterations=3,
    )

    reply = await orchestrator.generate_reply(
        messages=[],
        tools=[{"type": "function"}],
        registry=registry,
    )

    assert reply == "FALZH helps with travel booking."
    second_call_messages = primary.calls[1]["messages"]
    assert second_call_messages[-1]["role"] == "tool"
    assert '"ok": true' in second_call_messages[-1]["content"]


@pytest.mark.asyncio
async def test_ai_reports_invalid_tool_arguments_to_model():
    primary = ScriptedProvider(
        "groq",
        [
            AIProviderResponse(
                tool_calls=[ToolCall(id="call-1", name="about_falzh", arguments="{bad json")]
            ),
            AIProviderResponse(content="Please share the question again."),
        ],
    )
    registry = ToolRegistry()
    registry.register("about_falzh", ok_tool)
    orchestrator = AIOrchestrator(
        primary=primary,
        fallback=ScriptedProvider("hf", []),
        temperature=0.2,
        max_tool_iterations=3,
    )

    reply = await orchestrator.generate_reply(
        messages=[],
        tools=[{"type": "function"}],
        registry=registry,
    )

    assert reply == "Please share the question again."
    assert "Invalid tool arguments" in primary.calls[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_chat_falls_back_when_primary_rate_limited():
    primary = ScriptedProvider("groq", [RetryableProviderError("rate limited")])
    fallback = ScriptedProvider("gemini", [AIProviderResponse(content="fallback reply")])
    orchestrator = AIOrchestrator(
        primary=primary,
        fallback=fallback,
        temperature=0.2,
        max_tool_iterations=3,
    )

    response = await orchestrator.chat(
        messages=[{"role": "user", "content": "test"}],
    )

    assert response.content == "fallback reply"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_chat_returns_primary_on_success():
    primary = ScriptedProvider("groq", [AIProviderResponse(content="primary reply")])
    fallback = ScriptedProvider("gemini", [AIProviderResponse(content="fallback")])
    orchestrator = AIOrchestrator(
        primary=primary,
        fallback=fallback,
        temperature=0.2,
        max_tool_iterations=3,
    )

    response = await orchestrator.chat(
        messages=[{"role": "user", "content": "test"}],
    )

    assert response.content == "primary reply"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 0


@pytest.mark.asyncio
async def test_chat_retries_invalid_tool_call_before_fallback():
    primary = ScriptedProvider(
        "groq",
        [
            InvalidToolCallGenerationError("bad tool"),
            AIProviderResponse(content="primary retry reply"),
        ],
    )
    fallback = ScriptedProvider("gemini", [AIProviderResponse(content="fallback")])
    orchestrator = AIOrchestrator(
        primary=primary,
        fallback=fallback,
        temperature=0.4,
        max_tool_iterations=3,
    )

    response = await orchestrator.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=0.4,
    )

    assert response.content == "primary retry reply"
    assert [call["temperature"] for call in primary.calls] == [0.4, 0.2]
    assert fallback.calls == []
