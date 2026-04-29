"""Tests for AIService._parse_chat_response — robust ACTIONS extraction."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.ai_service import AIService  # noqa: E402


def parse(content: str):
    return AIService._parse_chat_response(content)


def test_no_actions_block_returns_full_message():
    msg, actions = parse("Hello world.")
    assert msg == "Hello world."
    assert actions == []


def test_single_actions_block_extracted():
    content = (
        "Here is your bid change.\n"
        '[ACTIONS]{"actions":[{"tool":"x","scope":"inline"}]}[/ACTIONS]'
    )
    msg, actions = parse(content)
    assert msg == "Here is your bid change."
    assert actions == [{"tool": "x", "scope": "inline"}]


def test_actions_block_inside_code_fence():
    content = (
        "Sure thing.\n"
        "[ACTIONS]\n"
        "```json\n"
        '{"actions":[{"tool":"a","scope":"inline"}]}\n'
        "```\n"
        "[/ACTIONS]"
    )
    msg, actions = parse(content)
    assert msg == "Sure thing."
    assert actions and actions[0]["tool"] == "a"


def test_bare_actions_array_supported():
    content = '[ACTIONS][{"tool":"a"},{"tool":"b"}][/ACTIONS]'
    msg, actions = parse(content)
    assert msg == ""
    assert [a["tool"] for a in actions] == ["a", "b"]


def test_multiple_actions_blocks_concatenated():
    content = (
        "First analysis.\n"
        '[ACTIONS]{"actions":[{"tool":"a"}]}[/ACTIONS]\n'
        "Then a second batch.\n"
        '[ACTIONS]{"actions":[{"tool":"b"}]}[/ACTIONS]\n'
        "Done."
    )
    msg, actions = parse(content)
    assert "First analysis." in msg
    assert "Then a second batch." in msg
    assert "Done." in msg
    assert "[ACTIONS]" not in msg
    assert [a["tool"] for a in actions] == ["a", "b"]


def test_invalid_json_in_block_is_skipped_with_message_preserved():
    content = "Body text.\n[ACTIONS]{not json[/ACTIONS]"
    msg, actions = parse(content)
    assert "Body text." in msg
    assert actions == []


def test_lowercase_tag_is_tolerated():
    content = '[actions]{"actions":[{"tool":"a"}]}[/actions]'
    msg, actions = parse(content)
    assert actions and actions[0]["tool"] == "a"
    assert msg == ""


def test_non_dict_actions_filtered_out():
    content = '[ACTIONS]{"actions":["oops",{"tool":"keep"},42]}[/ACTIONS]'
    _, actions = parse(content)
    assert actions == [{"tool": "keep"}]
