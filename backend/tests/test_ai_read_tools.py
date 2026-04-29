"""Tests for app.services.ai_read_tools.

Covers:
* Tool spec shape (OpenAI vs Anthropic) and required fields.
* :data:`READ_TOOL_NAMES` matches the published specs.
* :func:`build_tool_executor` dispatches to DB / MCP handlers and
  surfaces structured errors instead of raising into the chat loop.
* Lazy MCP client factory: not invoked until an ``mcp_*`` tool is hit.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import ai_read_tools as rt  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _make_db_dispatch_stub(name: str, fake_result: dict):
    async def _stub(args, *, db, cred):
        return {**fake_result, "_called_with": {"name": name, "args": args}}

    return _stub


def test_openai_specs_have_function_envelope():
    specs = rt.openai_read_tool_specs()
    assert specs
    for s in specs:
        assert s["type"] == "function"
        fn = s["function"]
        assert isinstance(fn["name"], str) and fn["name"]
        assert fn["parameters"]["type"] == "object"


def test_anthropic_specs_use_input_schema_envelope():
    specs = rt.anthropic_read_tool_specs()
    assert specs
    for s in specs:
        assert "name" in s
        assert "input_schema" in s
        assert "parameters" not in s


def test_read_tool_names_matches_published_specs():
    spec_names = {s["function"]["name"] for s in rt.openai_read_tool_specs()}
    assert spec_names == set(rt.READ_TOOL_NAMES)


def test_read_tool_names_split_db_vs_mcp():
    db_names = {n for n in rt.READ_TOOL_NAMES if n.startswith("db_")}
    mcp_names = {n for n in rt.READ_TOOL_NAMES if n.startswith("mcp_")}
    assert db_names, "expected db_* read tools"
    assert mcp_names, "expected mcp_* read tools"
    assert db_names | mcp_names == set(rt.READ_TOOL_NAMES)


def test_executor_unknown_tool_returns_error():
    executor = rt.build_tool_executor(db=object(), cred=object())
    result = _run(executor("not_a_tool", {}))
    assert "error" in result
    assert "Unknown" in result["error"]


def test_executor_dispatches_db_tool(monkeypatch):
    captured: dict = {}

    async def fake_db(args, *, db, cred):
        captured["args"] = args
        captured["db"] = db
        captured["cred"] = cred
        return {"source": "db", "table": "campaigns", "count": 0, "rows": []}

    monkeypatch.setitem(rt._DB_DISPATCH, "db_query_campaigns", fake_db)

    db = object()
    cred = object()
    executor = rt.build_tool_executor(db=db, cred=cred)
    out = _run(executor("db_query_campaigns", {"state": "ENABLED"}))
    assert out["source"] == "db"
    assert captured["args"] == {"state": "ENABLED"}
    assert captured["db"] is db
    assert captured["cred"] is cred


def test_executor_does_not_call_mcp_factory_for_db_tools(monkeypatch):
    factory = AsyncMock(return_value=SimpleNamespace())

    async def fake_db(args, *, db, cred):
        return {"source": "db", "rows": []}

    monkeypatch.setitem(rt._DB_DISPATCH, "db_query_targets", fake_db)
    executor = rt.build_tool_executor(
        db=object(), cred=object(), mcp_client_factory=factory,
    )
    _run(executor("db_query_targets", {}))
    factory.assert_not_called()


def test_executor_calls_mcp_factory_lazily_once(monkeypatch):
    fake_client = SimpleNamespace(
        query_campaigns=AsyncMock(return_value={"campaigns": [{"campaignId": "c1"}]}),
    )
    factory = AsyncMock(return_value=fake_client)

    executor = rt.build_tool_executor(
        db=object(), cred=object(), mcp_client_factory=factory,
    )
    out1 = _run(executor("mcp_list_campaigns", {"all_products": True}))
    out2 = _run(executor("mcp_list_campaigns", {"all_products": True}))

    assert out1["source"] == "mcp"
    assert out1["count"] == 1
    assert out2["source"] == "mcp"
    factory.assert_awaited_once()


def test_executor_no_mcp_factory_yields_structured_error(monkeypatch):
    executor = rt.build_tool_executor(db=object(), cred=object())
    out = _run(executor("mcp_list_campaigns", {}))
    assert "error" in out
    assert "MCP" in out["error"]


def test_executor_catches_handler_exceptions(monkeypatch):
    async def boom(args, *, db, cred):
        raise RuntimeError("boom")

    monkeypatch.setitem(rt._DB_DISPATCH, "db_query_ad_groups", boom)
    executor = rt.build_tool_executor(db=object(), cred=object())
    out = _run(executor("db_query_ad_groups", {}))
    assert "error" in out
    assert "db_query_ad_groups failed" in out["error"]


def test_clamp_limit_bounds():
    assert rt._clamp_limit(None) == 25
    assert rt._clamp_limit(0) == 1
    assert rt._clamp_limit("not-a-number") == 25
    assert rt._clamp_limit(9999) == rt.DB_ROW_CAP
    assert rt._clamp_limit(7) == 7
