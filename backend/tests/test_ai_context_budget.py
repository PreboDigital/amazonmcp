"""Tests for AIService context-budget caps and history trimming."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.ai_service import (  # noqa: E402
    AIService,
    MAX_CONTEXT_CHARS,
    MAX_HISTORY_CHARS,
    MAX_HISTORY_MESSAGES,
    SECTION_ROW_CAPS,
)


# ── _cap_context_sections ─────────────────────────────────────────────

def test_cap_context_returns_dict_for_non_dict():
    assert AIService._cap_context_sections([]) == {}
    assert AIService._cap_context_sections(None) == {}


def test_caps_all_campaigns_and_records_truncation():
    cap = SECTION_ROW_CAPS["all_campaigns"]
    ctx = {"all_campaigns": [{"name": f"c{i}"} for i in range(cap + 25)]}
    out = AIService._cap_context_sections(ctx)
    assert len(out["all_campaigns"]) == cap
    assert out["_truncations"]["all_campaigns"] == cap + 25


def test_caps_targets_subsections():
    cap = SECTION_ROW_CAPS["top_spenders"]
    ctx = {
        "targets_summary": {
            "top_spenders": [{"keyword": f"k{i}"} for i in range(cap + 5)],
            "top_converters": [{"keyword": "x"}],
        }
    }
    out = AIService._cap_context_sections(ctx)
    assert len(out["targets_summary"]["top_spenders"]) == cap
    assert out["_truncations"]["targets.top_spenders"] == cap + 5
    # Non-truncated section untouched
    assert len(out["targets_summary"]["top_converters"]) == 1


def test_no_truncation_when_within_caps():
    ctx = {"all_campaigns": [{"name": "only"}]}
    out = AIService._cap_context_sections(ctx)
    assert "_truncations" not in out


def test_full_context_message_truncated_when_over_global_cap():
    big_campaigns = [{
        "name": "C" * 1000,
        "state": "ENABLED",
        "type": "SP",
        "budget": 50,
    } for _ in range(SECTION_ROW_CAPS["all_campaigns"])]

    svc = object.__new__(AIService)  # bypass __init__ which needs API keys
    msg = AIService._build_context_message(svc, {
        "account": {"name": "Test"},
        "all_campaigns": big_campaigns,
    })
    assert len(msg) <= MAX_CONTEXT_CHARS + 200  # +slack for truncation footer


# ── _trim_conversation_history ───────────────────────────────────────

def test_trim_empty_history():
    assert AIService._trim_conversation_history([]) == []


def test_trim_keeps_recent_within_count_cap():
    history = [{"role": "user", "content": f"msg-{i}"} for i in range(MAX_HISTORY_MESSAGES + 10)]
    out = AIService._trim_conversation_history(history)
    assert len(out) <= MAX_HISTORY_MESSAGES
    # Most recent messages preserved
    assert out[-1]["content"] == history[-1]["content"]


def test_trim_drops_oldest_when_over_char_budget():
    big_msg = "x" * (MAX_HISTORY_CHARS // 4)
    history = [{"role": "user", "content": big_msg} for _ in range(20)]
    out = AIService._trim_conversation_history(history)
    # Should fit within budget — at most ~5 messages
    total_chars = sum(len(m["content"]) for m in out)
    assert total_chars <= MAX_HISTORY_CHARS + len(big_msg)  # last msg may overshoot
    assert out[-1]["content"] == big_msg


def test_trim_always_keeps_latest_message_even_if_oversize():
    huge = "z" * (MAX_HISTORY_CHARS * 2)
    history = [
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": huge},
    ]
    out = AIService._trim_conversation_history(history)
    assert out[-1]["content"] == huge
