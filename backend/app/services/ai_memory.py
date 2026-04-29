"""Rolling-summary memory for long AI conversations.

The legacy ``AIConversation`` model stored every turn verbatim. After
~20 turns the message log overflows the prompt budget and the assistant
either truncates context (loses earlier turns) or drops a giant
``messages`` array into the prompt and runs out of tokens.

This module provides the **compact-if-needed** flow:

1. Append the latest ``(role, content)`` turn to ``conversation.messages``.
2. When the trimmed tail still overflows the budget, summarise the
   oldest dropped turns into a single short string stored on
   ``conversation.head_summary`` and prune them from ``messages``.
3. ``messages_for_prompt(conversation)`` returns the prompt-ready
   message list = ``[head_summary as system message] + recent turns``.

Both summarisation paths (LLM-driven and a deterministic fallback) are
supported. When no AI service is available we just deduplicate the
oldest turns into a heuristic line so the row size stays bounded.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import AIConversation

logger = logging.getLogger(__name__)


# Tunables — picked to match ``ai_service.MAX_HISTORY_*`` so trimming
# matches the chat path's own budget without round-tripping.
MAX_TURNS_KEPT = 30
MAX_HISTORY_CHARS = 16_000
SUMMARY_BUDGET_CHARS = 1_500
SUMMARY_TARGET_TURNS = 10  # how many turns of context the summary tries to compress


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalise_messages(messages) -> list[dict]:
    if not isinstance(messages, list):
        return []
    out: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or "user"
        content = msg.get("content")
        if not isinstance(content, str):
            content = str(content) if content is not None else ""
        out.append({
            "role": role,
            "content": content,
            "timestamp": msg.get("timestamp") or _now_iso(),
        })
    return out


def _conversation_too_big(messages: list[dict]) -> bool:
    if len(messages) > MAX_TURNS_KEPT:
        return True
    char_count = sum(len(m.get("content", "")) for m in messages)
    return char_count > MAX_HISTORY_CHARS


def _heuristic_summary(turns: list[dict], existing_head: Optional[str]) -> str:
    """Deterministic fallback summary — used when no LLM is available.

    Keeps the *first user question* and the *last assistant answer* in
    the dropped window — enough signal for context continuity without
    sending the model anything fabricated.
    """
    if not turns:
        return existing_head or ""
    first_user = next(
        (m for m in turns if m.get("role") == "user" and m.get("content")),
        None,
    )
    last_assistant = next(
        (m for m in reversed(turns) if m.get("role") == "assistant" and m.get("content")),
        None,
    )
    parts: list[str] = []
    if existing_head:
        parts.append(existing_head.strip())
    parts.append(f"Earlier conversation ({len(turns)} turns) summarised:")
    if first_user:
        parts.append(f"User asked: {first_user['content'][:400]}")
    if last_assistant:
        parts.append(f"Assistant answered: {last_assistant['content'][:400]}")
    summary = "\n".join(p for p in parts if p)
    return summary[:SUMMARY_BUDGET_CHARS]


async def _llm_summary(
    turns: list[dict],
    existing_head: Optional[str],
    ai_service,
) -> Optional[str]:
    """Ask the AI service for a tight summary; ``None`` on any failure."""
    if not turns or ai_service is None:
        return None
    transcript_parts: list[str] = []
    if existing_head:
        transcript_parts.append(f"Previous summary:\n{existing_head}")
    transcript_parts.append("Conversation excerpt to summarise:")
    for m in turns:
        role = m.get("role") or "user"
        content = (m.get("content") or "")[:1500]
        transcript_parts.append(f"[{role}] {content}")
    transcript = "\n".join(transcript_parts)
    prompt = (
        "Summarise the conversation excerpt below into a single short paragraph "
        "(<=1200 chars) capturing the user's intent, any account IDs mentioned, "
        "decisions made, and pending follow-ups. Do not invent data. Reply with "
        "the summary text only."
    )
    try:
        result = await ai_service._completion_full(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": transcript[:12_000]},
            ],
            temperature=0.0,
            max_tokens=400,
        )
    except Exception as exc:
        logger.warning("ai_memory llm summary failed: %s", exc)
        return None
    text = (result.get("content") or "").strip()
    return text[:SUMMARY_BUDGET_CHARS] or None


async def compact_if_needed(
    conversation: AIConversation,
    db: AsyncSession,
    *,
    ai_service=None,
) -> bool:
    """Trim the oldest turns into ``head_summary`` when the log is too big.

    Returns True when state was modified. Caller commits.
    """
    messages = _normalise_messages(conversation.messages)
    if not _conversation_too_big(messages):
        return False

    # Keep at most ``MAX_TURNS_KEPT // 2`` turns by count, and additionally
    # trim from the head until the remaining tail fits in
    # ``MAX_HISTORY_CHARS // 2`` chars — so a few very long turns also
    # trigger summarisation, not just a long count.
    by_count_keep = max(1, MAX_TURNS_KEPT // 2)
    char_target = MAX_HISTORY_CHARS // 2

    tail = messages[-by_count_keep:]
    while len(tail) > 1 and sum(len(m.get("content", "")) for m in tail) > char_target:
        tail = tail[1:]

    drop_cutoff = len(messages) - len(tail)
    dropped = messages[:drop_cutoff]

    if not dropped:
        return False

    summary: Optional[str] = None
    if ai_service is not None:
        # Use only the most recent ``SUMMARY_TARGET_TURNS`` from the
        # dropped window — sending all 100+ would defeat the point.
        recent_dropped = dropped[-SUMMARY_TARGET_TURNS:]
        summary = await _llm_summary(
            recent_dropped, conversation.head_summary, ai_service
        )
    if not summary:
        summary = _heuristic_summary(dropped, conversation.head_summary)

    conversation.head_summary = summary
    conversation.messages = tail
    if hasattr(conversation, "_sa_instance_state"):
        flag_modified(conversation, "messages")
    db.add(conversation)
    return True


def messages_for_prompt(
    conversation: AIConversation,
) -> list[dict]:
    """Return the message list to feed into the chat completion API.

    Prepends ``head_summary`` as a synthetic ``system`` turn when set so
    the model sees both the rolling summary and the live tail.
    """
    out: list[dict] = []
    head = (conversation.head_summary or "").strip() if conversation else ""
    if head:
        out.append({
            "role": "system",
            "content": (
                "Earlier conversation summary (use for continuity, do not quote "
                f"verbatim):\n{head}"
            ),
        })
    out.extend(_normalise_messages(conversation.messages if conversation else []))
    return out


def append_turn(
    conversation: AIConversation,
    role: str,
    content: str,
) -> None:
    """Mutating helper — appends one (role, content) turn."""
    msgs = _normalise_messages(conversation.messages)
    msgs.append({
        "role": role,
        "content": content,
        "timestamp": _now_iso(),
    })
    conversation.messages = msgs
    if hasattr(conversation, "_sa_instance_state"):
        flag_modified(conversation, "messages")


__all__ = [
    "compact_if_needed",
    "messages_for_prompt",
    "append_turn",
    "MAX_TURNS_KEPT",
    "MAX_HISTORY_CHARS",
    "SUMMARY_BUDGET_CHARS",
]
