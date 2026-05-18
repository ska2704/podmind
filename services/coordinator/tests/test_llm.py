"""LLM tool-loop tests. We mock Ollama's /api/chat with respx and
inspect what messages get sent in each round."""

import httpx
import pytest
import respx

from app.llm import LLMError, ask


OLLAMA = "http://ollama.test:11434"
MODEL = "qwen2.5:1.5b-instruct-q4_K_M"

DUMMY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_pod_metrics",
            "description": "...",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
]


def _chat_response(content: str = "", tool_calls=None) -> dict:
    return {
        "model": MODEL,
        "message": {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls or [],
        },
        "eval_count": 1,
        "total_duration": 100_000_000,
    }


@respx.mock
async def test_ask_no_tool_calls_returns_content_directly():
    respx.post(f"{OLLAMA}/api/chat").mock(
        return_value=httpx.Response(200, json=_chat_response(content="Looks fine."))
    )
    calls: list = []

    async def dispatch(name, args):
        calls.append((name, args))
        return {}

    async with httpx.AsyncClient() as client:
        result = await ask(
            client=client,
            ollama_url=OLLAMA,
            model=MODEL,
            question="any?",
            tools=DUMMY_TOOLS,
            dispatch=dispatch,
            max_rounds=5,
        )
    assert result == {"answer": "Looks fine.", "tools_called": []}
    assert calls == []


@respx.mock
async def test_ask_runs_one_tool_then_answers():
    # First call: model asks for a tool. Second call: model returns text.
    responses = [
        httpx.Response(200, json=_chat_response(tool_calls=[
            {
                "id": "tc1",
                "function": {
                    "name": "get_pod_metrics",
                    "arguments": {"pod": "hvac-controller", "since_s": 120},
                },
            }
        ])),
        httpx.Response(200, json=_chat_response(content="hvac-controller CPU is high.")),
    ]
    respx.post(f"{OLLAMA}/api/chat").mock(side_effect=responses)

    dispatched: list = []

    async def dispatch(name, args):
        dispatched.append((name, args))
        return {"summary": {"current": 0.2}}

    async with httpx.AsyncClient() as client:
        result = await ask(
            client=client,
            ollama_url=OLLAMA,
            model=MODEL,
            question="why is hvac hot?",
            tools=DUMMY_TOOLS,
            dispatch=dispatch,
            max_rounds=5,
        )

    assert dispatched == [("get_pod_metrics", {"pod": "hvac-controller", "since_s": 120})]
    assert result["answer"] == "hvac-controller CPU is high."
    assert result["tools_called"] == [
        {"name": "get_pod_metrics", "arguments": {"pod": "hvac-controller", "since_s": 120}}
    ]


@respx.mock
async def test_ask_handles_string_arguments_field():
    """Some Ollama versions send arguments as a JSON string; normalize it."""
    responses = [
        httpx.Response(200, json=_chat_response(tool_calls=[
            {
                "id": "tc",
                "function": {
                    "name": "get_pod_metrics",
                    "arguments": '{"pod":"hvac","since_s":60}',  # JSON-string variant
                },
            }
        ])),
        httpx.Response(200, json=_chat_response(content="ok.")),
    ]
    respx.post(f"{OLLAMA}/api/chat").mock(side_effect=responses)

    dispatched: list = []

    async def dispatch(name, args):
        dispatched.append((name, args))
        return {}

    async with httpx.AsyncClient() as client:
        await ask(
            client=client,
            ollama_url=OLLAMA,
            model=MODEL,
            question="?",
            tools=DUMMY_TOOLS,
            dispatch=dispatch,
            max_rounds=3,
        )
    assert dispatched == [("get_pod_metrics", {"pod": "hvac", "since_s": 60})]


@respx.mock
async def test_ask_enforces_round_limit():
    # Model never stops asking for tools — we should bail.
    respx.post(f"{OLLAMA}/api/chat").mock(
        return_value=httpx.Response(200, json=_chat_response(tool_calls=[
            {"id": "x", "function": {"name": "get_pod_metrics", "arguments": {"pod": "p"}}}
        ]))
    )

    async def dispatch(name, args):
        return {}

    async with httpx.AsyncClient() as client:
        with pytest.raises(LLMError):
            await ask(
                client=client,
                ollama_url=OLLAMA,
                model=MODEL,
                question="?",
                tools=DUMMY_TOOLS,
                dispatch=dispatch,
                max_rounds=2,
            )


@respx.mock
async def test_ask_appends_tool_error_message_and_continues():
    """When a tool raises, we record an error result and let the model decide."""
    responses = [
        httpx.Response(200, json=_chat_response(tool_calls=[
            {"id": "tc", "function": {"name": "get_pod_metrics", "arguments": {"pod": "x"}}}
        ])),
        httpx.Response(200, json=_chat_response(content="couldn't read metrics.")),
    ]
    respx.post(f"{OLLAMA}/api/chat").mock(side_effect=responses)

    async def dispatch(name, args):
        raise RuntimeError("ingestor down")

    async with httpx.AsyncClient() as client:
        result = await ask(
            client=client,
            ollama_url=OLLAMA,
            model=MODEL,
            question="?",
            tools=DUMMY_TOOLS,
            dispatch=dispatch,
            max_rounds=3,
        )
    assert result["answer"] == "couldn't read metrics."
    assert result["tools_called"][0]["name"] == "get_pod_metrics"
