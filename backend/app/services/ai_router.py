"""Lite intent classifier for the AI assistant.

Routes the user's question to one of the specialised buckets so the
chat path can:

* tighten the system prompt (e.g. only mention bid bounds when the
  intent is bid optimization);
* select which slices of context to load (pulling search-term rows for
  a "harvest" intent, daily performance for "reporting", etc.);
* skip context entirely for pure "general" questions and avoid the
  60-KB dump that bloats latency.

This is a deliberate **lite** router — heuristic-first, with a single
optional LLM classifier call as a tie-breaker. Phase 3 will replace it
with a per-agent dispatcher; for now we just need a clean interface.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


VALID_AGENTS: frozenset[str] = frozenset({
    "reporting",       # "show me last 7 days spend", "ACOS by campaign"
    "optimization",    # bid / budget changes, ACOS targeting
    "creation",        # new campaign / ad group / keyword from scratch
    "harvest",         # promote search terms to keywords
    "audit",           # account health, anomalies, waste detection
    "general",         # everything else — use a generic short prompt
})


# Keyword fingerprints — tuned conservatively so a clear intent wins
# and ambiguous cases fall through to the LLM tie-breaker.
_HEURISTICS: list[tuple[str, list[str]]] = [
    ("creation", [
        "create campaign", "new campaign", "launch campaign",
        "build campaign", "set up campaign", "create ad group",
        "new ad group",
    ]),
    ("harvest", [
        "harvest", "graduate", "promote search term", "convert search term",
        "negative keyword", "negate", "exact match graduation",
    ]),
    ("audit", [
        "audit", "health", "anomaly", "anomalies", "waste",
        "non-converting", "what's wrong", "broken", "issues",
    ]),
    ("optimization", [
        "raise bid", "lower bid", "increase bid", "decrease bid",
        "raise the bid", "lower the bid", "increase the bid", "decrease the bid",
        "adjust bid", "bid change", "bid up", "bid down",
        "budget change", "increase budget", "lower budget",
        "pause campaign", "enable campaign", "pause it", "enable it",
        "optimize", "improve acos", "reduce acos", "target acos",
        "high-acos", "high acos",
    ]),
    ("reporting", [
        "report", "spend", "sales", "acos", "roas", "ctr", "cpc", "impressions",
        "clicks", "orders", "performance", "trend", "yesterday",
        "last 7 days", "last week", "last month", "today", "compare",
        "show me", "list", "top",
    ]),
]


@dataclass
class RouteDecision:
    """Outcome of :func:`classify_intent`."""

    agent: str
    confidence: float = 0.5
    matched_keywords: list[str] = field(default_factory=list)
    needs_account_context: bool = True
    needs_performance_context: bool = False
    needs_search_term_context: bool = False
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "confidence": self.confidence,
            "matched_keywords": self.matched_keywords,
            "needs_account_context": self.needs_account_context,
            "needs_performance_context": self.needs_performance_context,
            "needs_search_term_context": self.needs_search_term_context,
            "notes": self.notes,
        }


def _word_match(text_lower: str, phrase: str) -> bool:
    """Return True iff ``phrase`` appears as a whole-word substring."""
    if " " in phrase:
        return phrase in text_lower
    return re.search(rf"\b{re.escape(phrase)}\b", text_lower) is not None


def classify_intent(message: str) -> RouteDecision:
    """Heuristic classifier — fast, deterministic, no network call.

    The first agent whose fingerprint hits wins. If nothing matches we
    return ``general`` with low confidence so callers can either route
    to a generic short prompt or fall back to an LLM tie-breaker.
    """
    if not isinstance(message, str) or not message.strip():
        return RouteDecision(
            agent="general",
            confidence=0.4,
            needs_account_context=False,
            notes="empty message",
        )
    text = message.lower()

    matched_for: dict[str, list[str]] = {agent: [] for agent, _ in _HEURISTICS}
    for agent, phrases in _HEURISTICS:
        for phrase in phrases:
            if _word_match(text, phrase):
                matched_for[agent].append(phrase)

    # Pick the agent with the most hits; tie-breaker = order of _HEURISTICS
    best_agent: Optional[str] = None
    best_count = 0
    for agent, _ in _HEURISTICS:
        hits = len(matched_for[agent])
        if hits > best_count:
            best_agent = agent
            best_count = hits

    if best_agent is None or best_count == 0:
        return RouteDecision(
            agent="general",
            confidence=0.45,
            needs_account_context=False,
            notes="no fingerprint matched",
        )

    confidence = min(0.95, 0.55 + 0.1 * best_count)
    needs_perf = best_agent in {"reporting", "optimization", "audit"}
    needs_st = best_agent in {"harvest", "audit", "optimization"}

    return RouteDecision(
        agent=best_agent,
        confidence=confidence,
        matched_keywords=matched_for[best_agent],
        needs_account_context=True,
        needs_performance_context=needs_perf,
        needs_search_term_context=needs_st,
    )


# ── Optional LLM tie-breaker ─────────────────────────────────────────

_TIE_BREAK_SYSTEM = (
    "You are an intent classifier for an Amazon Ads assistant. "
    "Reply with exactly one word from this list and nothing else: "
    "reporting, optimization, creation, harvest, audit, general."
)


async def llm_tie_break(message: str, ai_service) -> Optional[str]:
    """Single LLM call to pick the agent when heuristics are ambiguous.

    Returns the agent name on success, ``None`` when the model returned
    something off-list. Caller is expected to fall back to ``general``.
    """
    try:
        result = await ai_service._completion_full(
            messages=[
                {"role": "system", "content": _TIE_BREAK_SYSTEM},
                {"role": "user", "content": message[:1000]},
            ],
            temperature=0.0,
            max_tokens=8,
        )
    except Exception as exc:
        logger.warning("LLM tie-break failed: %s", exc)
        return None
    raw = (result.get("content") or "").strip().lower()
    word = re.split(r"\W+", raw)[0] if raw else ""
    return word if word in VALID_AGENTS else None


__all__ = ["classify_intent", "llm_tie_break", "RouteDecision", "VALID_AGENTS"]
