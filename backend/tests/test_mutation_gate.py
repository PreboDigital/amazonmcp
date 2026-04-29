"""Tests for app.services.mutation_gate.

Phase 5+ unifies all tool dispatch behind ``run_tool``. These tests
cover the gate's classification + sanitisation invariants.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import mutation_gate as gate  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# ── Classification ───────────────────────────────────────────────────

def test_is_mutation_recognises_known_writes():
    assert gate.is_mutation("campaign_management-update_target_bid")
    assert gate.is_mutation("campaign_management-create_target")
    assert gate.is_mutation("campaign_management-delete_target")
    assert gate.is_mutation("_harvest_execute")


def test_is_mutation_recognises_unenumerated_writes_via_prefix():
    assert gate.is_mutation("campaign_management-update_future_thing")
    assert gate.is_mutation("campaign_management-set_priority")


def test_is_mutation_negative_for_reads():
    assert not gate.is_mutation("campaign_management-query_target")
    assert not gate.is_mutation("reporting-create_campaign_report")  # report runs are reads


def test_is_mutation_handles_invalid_input():
    assert not gate.is_mutation(None)
    assert not gate.is_mutation("")


# ── Sanitiser ────────────────────────────────────────────────────────

def test_sanitize_truncates_oversized_target_list():
    args = {"body": {"targets": [{"targetId": str(i), "bid": 0.5} for i in range(gate.MAX_TARGETS_PER_CALL + 50)]}}
    clean, warnings = gate.sanitize_mutation_queue_args(
        "campaign_management-update_target_bid", args
    )
    assert len(clean["body"]["targets"]) == gate.MAX_TARGETS_PER_CALL
    assert any("truncated" in w for w in warnings)


def test_sanitize_rejects_oversized_payload():
    args = {"body": {"campaigns": [{"campaignId": "c", "name": "X" * 200}]}}
    args["body"]["campaigns"].extend(
        [{"campaignId": str(i), "name": "X" * 1000} for i in range(50)]
    )
    args["body"]["very_long_payload"] = "x" * 60_000
    clean, warnings = gate.sanitize_mutation_queue_args(
        "campaign_management-update_campaign", args
    )
    assert clean == {}
    assert any("exceed" in w for w in warnings)


def test_sanitize_passes_normal_payload_through():
    args = {"body": {"targets": [{"targetId": "t-1", "bid": 0.5}]}}
    clean, warnings = gate.sanitize_mutation_queue_args(
        "campaign_management-update_target_bid", args
    )
    assert clean == args
    assert warnings == []


# ── Dispatcher ───────────────────────────────────────────────────────

def test_run_tool_blocks_mutations_without_allow_flag():
    client = AsyncMock()
    result = _run(gate.run_tool(
        client,
        "campaign_management-update_target_bid",
        {"body": {"targets": [{"targetId": "t-1", "bid": 0.5}]}},
        allow_mutations=False,
    ))
    assert result.ok is True
    assert result.requires_human_approval is True
    assert client.call_tool.await_count == 0


def test_run_tool_executes_mutation_when_allowed():
    client = AsyncMock()
    client.call_tool.return_value = {"ok": True}
    result = _run(gate.run_tool(
        client,
        "campaign_management-update_target_bid",
        {"body": {"targets": [{"targetId": "t-1", "bid": 0.5}]}},
        allow_mutations=True,
    ))
    assert result.ok is True
    assert result.requires_human_approval is False
    assert result.result == {"ok": True}
    client.call_tool.assert_awaited_once()


def test_run_tool_executes_reads_immediately():
    client = AsyncMock()
    client.call_tool.return_value = {"campaigns": []}
    result = _run(gate.run_tool(
        client, "campaign_management-query_campaign", {"body": {}},
    ))
    assert result.ok is True
    assert result.requires_human_approval is False
    assert result.result == {"campaigns": []}


def test_run_tool_rejects_unknown_synthetic_tool():
    client = AsyncMock()
    result = _run(gate.run_tool(
        client, "_not_a_real_synthetic", {"foo": "bar"},
    ))
    assert result.ok is False
    assert "Unknown" in (result.error or "")


def test_run_tool_synthetic_returns_human_approval_envelope():
    client = AsyncMock()
    result = _run(gate.run_tool(
        client, "_request_sync", {"kind": "reports"},
    ))
    assert result.ok is True
    assert result.requires_human_approval is True


def test_run_tool_propagates_call_tool_exception_as_error():
    client = AsyncMock()
    client.call_tool.side_effect = RuntimeError("boom")
    result = _run(gate.run_tool(
        client, "campaign_management-query_campaign", {},
    ))
    assert result.ok is False
    assert "boom" in (result.error or "")


def test_run_tool_rejects_oversized_mutation_payload():
    """When the sanitiser empties a non-empty payload the gate must hard-fail."""
    client = AsyncMock()
    bad_args = {"body": {"campaigns": [{"campaignId": "c", "name": "X" * 200}]}}
    bad_args["body"]["very_long_payload"] = "x" * 60_000
    result = _run(gate.run_tool(
        client,
        "campaign_management-update_campaign",
        bad_args,
        allow_mutations=True,
    ))
    assert result.ok is False
    assert "oversized" in (result.error or "")
    client.call_tool.assert_not_awaited()


def test_run_tool_rejects_oversized_synthetic_payload():
    client = AsyncMock()
    bad_args = {"plan": {"x": "y" * 60_000}}
    result = _run(gate.run_tool(
        client, "_ai_campaign_create", bad_args,
    ))
    assert result.ok is False
    assert "oversized" in (result.error or "")
