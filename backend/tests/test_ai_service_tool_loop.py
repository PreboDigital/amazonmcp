"""Tests for AIService._chat_openai_tool_loop — multi-turn tool use.

Stubs the OpenAI client so we can deterministically assert:
* read tool calls execute and feed results back to the model
* mutation tool calls end the loop and surface as actions
* hop budget is enforced
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import ai_service as svc  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _mk_choice(content="", tool_calls=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls or []),
            )
        ],
        usage=SimpleNamespace(total_tokens=0),
    )


def _mk_tool_call(call_id, name, args):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _service():
    """Build an AIService without touching real provider clients."""
    s = svc.AIService.__new__(svc.AIService)
    s.provider = "openai"
    s.model = "gpt-test"
    s._openai_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
    )
    s._anthropic_client = None
    return s


def test_tool_loop_executes_reads_and_loops_to_final_text():
    s = _service()
    s._openai_client.chat.completions.create.side_effect = [
        _mk_choice(
            content="",
            tool_calls=[
                _mk_tool_call("call-1", "db_query_campaigns", {"state": "ENABLED"}),
            ],
        ),
        _mk_choice(content="You have 2 enabled campaigns. Top spender is X."),
    ]
    executor_calls: list = []

    async def executor(name, args):
        executor_calls.append((name, args))
        return {"source": "db", "rows": [{"name": "X"}, {"name": "Y"}]}

    out = _run(
        s._chat_openai_tool_loop(
            messages=[{"role": "user", "content": "list campaigns"}],
            tool_executor=executor,
        )
    )

    assert out["message"].startswith("You have 2 enabled campaigns")
    assert out["actions"] == []
    assert out["tool_hops"] == 1
    assert executor_calls == [("db_query_campaigns", {"state": "ENABLED"})]


def test_tool_loop_returns_actions_when_model_emits_mutation():
    s = _service()
    s._openai_client.chat.completions.create.side_effect = [
        _mk_choice(
            content="Pausing campaign c1.",
            tool_calls=[
                _mk_tool_call(
                    "call-mut",
                    "campaign_management-update_campaign_state",
                    {
                        "body": {
                            "campaigns": [{"campaignId": "c1", "state": "PAUSED"}],
                        }
                    },
                ),
            ],
        ),
    ]

    async def executor(name, args):
        raise AssertionError("read executor should not be called for mutations")

    out = _run(
        s._chat_openai_tool_loop(
            messages=[{"role": "user", "content": "pause c1"}],
            tool_executor=executor,
        )
    )
    assert out["message"] == "Pausing campaign c1."
    assert len(out["actions"]) == 1
    act = out["actions"][0]
    assert act["tool"] == "campaign_management-update_campaign_state"
    assert act["scope"] == "inline"
    assert out["tool_hops"] == 0


def test_tool_loop_caps_hops_and_returns_apology():
    s = _service()

    # Always reply with the same read tool call → loop forever.
    def _always_read(*_args, **_kwargs):
        return _mk_choice(
            content="",
            tool_calls=[_mk_tool_call("call-x", "db_query_campaigns", {})],
        )

    s._openai_client.chat.completions.create = AsyncMock(side_effect=_always_read)

    async def executor(name, args):
        return {"source": "db", "rows": []}

    out = _run(
        s._chat_openai_tool_loop(
            messages=[{"role": "user", "content": "loop"}],
            tool_executor=executor,
        )
    )
    assert out["tool_hops"] == svc.MAX_TOOL_HOPS
    assert "more specific" in out["message"].lower()


def test_tool_loop_handles_executor_exceptions_gracefully():
    s = _service()
    s._openai_client.chat.completions.create.side_effect = [
        _mk_choice(
            content="",
            tool_calls=[_mk_tool_call("call-fail", "db_query_targets", {})],
        ),
        _mk_choice(content="Couldn't fetch targets — fallback answer."),
    ]

    async def executor(name, args):
        raise RuntimeError("DB exploded")

    out = _run(
        s._chat_openai_tool_loop(
            messages=[{"role": "user", "content": "show targets"}],
            tool_executor=executor,
        )
    )
    assert "fallback answer" in out["message"]
    assert out["tool_hops"] == 1


def test_chat_falls_back_to_single_pass_for_anthropic(monkeypatch):
    s = svc.AIService.__new__(svc.AIService)
    s.provider = "anthropic"
    s.model = "claude-test"
    s._openai_client = None
    s._anthropic_client = object()
    s._completion_full = AsyncMock(
        return_value={"content": "anthropic single-pass reply", "tool_calls": []}
    )

    out = _run(
        s.chat(
            user_message="hi",
            conversation_history=[],
            account_context=None,
            tool_executor=AsyncMock(),  # ignored because provider != openai
        )
    )
    assert out["message"] == "anthropic single-pass reply"
    assert out["tool_hops"] == 0
