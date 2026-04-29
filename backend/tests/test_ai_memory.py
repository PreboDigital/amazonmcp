"""Tests for app.services.ai_memory rolling-summary memory."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import ai_memory as mem  # noqa: E402


def _conv(messages=None, head_summary=None):
    return SimpleNamespace(
        id="conv-1",
        messages=messages or [],
        head_summary=head_summary,
    )


def _run(coro):
    return asyncio.run(coro)


def test_compact_noop_when_under_budget():
    conv = _conv(messages=[{"role": "user", "content": "hi"}])
    db = MagicMock()
    changed = _run(mem.compact_if_needed(conv, db))
    assert changed is False
    assert conv.head_summary is None


def test_compact_summarises_oldest_when_too_many_turns():
    msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"}
        for i in range(mem.MAX_TURNS_KEPT + 10)
    ]
    conv = _conv(messages=msgs)
    db = MagicMock()
    changed = _run(mem.compact_if_needed(conv, db))
    assert changed is True
    assert isinstance(conv.head_summary, str) and conv.head_summary
    assert len(conv.messages) <= mem.MAX_TURNS_KEPT


def test_compact_summarises_when_char_budget_exceeded():
    big = "x" * (mem.MAX_HISTORY_CHARS // 4)
    msgs = [{"role": "user", "content": big} for _ in range(8)]
    conv = _conv(messages=msgs)
    db = MagicMock()
    changed = _run(mem.compact_if_needed(conv, db))
    assert changed is True
    assert conv.head_summary is not None


def test_messages_for_prompt_prepends_summary_when_set():
    conv = _conv(
        messages=[{"role": "user", "content": "hello"}],
        head_summary="Earlier summary blob",
    )
    msgs = mem.messages_for_prompt(conv)
    assert msgs[0]["role"] == "system"
    assert "Earlier summary blob" in msgs[0]["content"]
    assert msgs[-1]["content"] == "hello"


def test_append_turn_adds_timestamp():
    conv = _conv(messages=[])
    mem.append_turn(conv, "user", "hi there")
    assert conv.messages[0]["role"] == "user"
    assert conv.messages[0]["content"] == "hi there"
    assert "timestamp" in conv.messages[0]


def test_compact_uses_llm_summary_when_service_provided():
    msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i} " * 50}
        for i in range(mem.MAX_TURNS_KEPT + 5)
    ]
    conv = _conv(messages=msgs)
    db = MagicMock()
    fake_ai = SimpleNamespace()
    fake_ai._completion_full = AsyncMock(
        return_value={"content": "FAKE LLM SUMMARY", "tool_calls": []}
    )
    changed = _run(mem.compact_if_needed(conv, db, ai_service=fake_ai))
    assert changed is True
    assert "FAKE LLM SUMMARY" in (conv.head_summary or "")
    assert fake_ai._completion_full.await_count == 1


def test_compact_falls_back_when_llm_summary_empty():
    msgs = [
        {"role": "user", "content": f"q-{i}"}
        for i in range(mem.MAX_TURNS_KEPT + 3)
    ]
    conv = _conv(messages=msgs)
    db = MagicMock()
    fake_ai = SimpleNamespace()
    fake_ai._completion_full = AsyncMock(return_value={"content": "", "tool_calls": []})
    _run(mem.compact_if_needed(conv, db, ai_service=fake_ai))
    # Falls back to heuristic summary, which contains "Earlier conversation"
    assert "Earlier conversation" in (conv.head_summary or "")
