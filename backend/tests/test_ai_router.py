"""Tests for app.services.ai_router.classify_intent."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import ai_router as router  # noqa: E402


def test_reporting_intent_for_metric_question():
    d = router.classify_intent("Show me the top 10 campaigns by spend last 7 days")
    assert d.agent == "reporting"
    assert d.needs_performance_context


def test_optimization_intent_for_bid_question():
    d = router.classify_intent("Lower the bid on my high-ACOS keywords")
    assert d.agent == "optimization"
    assert d.needs_performance_context


def test_creation_intent():
    d = router.classify_intent("Create a new campaign for ASIN B0XYZ123")
    assert d.agent == "creation"


def test_harvest_intent():
    d = router.classify_intent("Harvest converting search terms into a new exact campaign")
    assert d.agent == "harvest"
    assert d.needs_search_term_context


def test_audit_intent():
    d = router.classify_intent("Run an audit, what's wrong with my account?")
    assert d.agent == "audit"


def test_general_for_no_match():
    d = router.classify_intent("Tell me a joke about hummingbirds")
    assert d.agent == "general"
    assert not d.needs_account_context


def test_empty_input_returns_general():
    d = router.classify_intent("")
    assert d.agent == "general"


def test_higher_confidence_with_more_keyword_hits():
    one = router.classify_intent("Show me spend")
    many = router.classify_intent(
        "Show me spend, sales, ACOS, ROAS, CTR and CPC for last 7 days"
    )
    assert many.confidence > one.confidence
