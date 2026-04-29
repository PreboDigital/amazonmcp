"""Tests for AmazonAdsMCP body/header scoping + error parsing.

Covers Phase 0 fixes:
- Body-level accessRequestedAccount(s) must be stripped when fixed-scope
  headers (Amazon-Advertising-API-Scope or Amazon-Ads-AccountID with
  Amazon-Ads-AI-Account-Selection-Mode: FIXED) are sent. The MCP server
  rejects body scoping in that mode with: "Cannot pass accessRequestedAccounts
  in body when using fixed account scope headers".
- Plain-prose 200-OK error bodies must surface as MCPError instead of
  silently returning {"result": "<error string>"}.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Allow importing app/ when tests are run from backend/.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.mcp_client import AmazonAdsMCP, MCPError  # noqa: E402


@pytest.fixture
def fixed_scope_client() -> AmazonAdsMCP:
    return AmazonAdsMCP(
        client_id="amzn1.application-oa2-client.test",
        access_token="Atza|test",
        region="eu",
        profile_id="1690567693689407",
    )


@pytest.fixture
def open_scope_client() -> AmazonAdsMCP:
    client = AmazonAdsMCP(
        client_id="amzn1.application-oa2-client.test",
        access_token="Atza|test",
        region="eu",
    )
    client.set_advertiser_account_id("ENTITY1234")
    return client


# ── _has_fixed_scope_headers / headers ─────────────────────────────────


def test_fixed_scope_headers_set_when_profile_id_present(fixed_scope_client):
    assert fixed_scope_client._has_fixed_scope_headers() is True
    headers = fixed_scope_client.headers
    assert headers["Amazon-Advertising-API-Scope"] == "1690567693689407"
    assert headers["Amazon-Ads-AI-Account-Selection-Mode"] == "FIXED"


def test_open_scope_has_no_fixed_headers(open_scope_client):
    assert open_scope_client._has_fixed_scope_headers() is False
    assert "Amazon-Ads-AI-Account-Selection-Mode" not in open_scope_client.headers


# ── _apply_access_requested_account ────────────────────────────────────


def test_apply_access_strips_when_fixed_scope(fixed_scope_client):
    body = {
        "adProductFilter": {"include": ["SPONSORED_PRODUCTS"]},
        "accessRequestedAccount": {"advertiserAccountId": "OLD"},
        "accessRequestedAccounts": [{"advertiserAccountId": "OLD"}],
    }
    out = fixed_scope_client._apply_access_requested_account(body)
    assert "accessRequestedAccount" not in out
    assert "accessRequestedAccounts" not in out
    assert out["adProductFilter"] == {"include": ["SPONSORED_PRODUCTS"]}


def test_apply_access_attaches_when_open_scope(open_scope_client):
    body = {"adProductFilter": {"include": ["SPONSORED_PRODUCTS"]}}
    out = open_scope_client._apply_access_requested_account(body)
    assert out["accessRequestedAccount"] == {"advertiserAccountId": "ENTITY1234"}


def test_apply_access_preserves_existing_open_scope_override(open_scope_client):
    existing = {"advertiserAccountId": "OVERRIDE"}
    body = {"accessRequestedAccount": existing}
    out = open_scope_client._apply_access_requested_account(body)
    assert out["accessRequestedAccount"] is existing


# ── _sanitize_arguments (call_tool gate) ───────────────────────────────


def test_sanitize_strips_body_account_when_fixed_scope(fixed_scope_client):
    args = {
        "body": {
            "campaigns": [{"campaignId": "1"}],
            "accessRequestedAccount": {"advertiserAccountId": "X"},
            "accessRequestedAccounts": [{"advertiserAccountId": "X"}],
        }
    }
    out = fixed_scope_client._sanitize_arguments(args)
    assert "accessRequestedAccount" not in out["body"]
    assert "accessRequestedAccounts" not in out["body"]
    assert out["body"]["campaigns"] == [{"campaignId": "1"}]
    assert "accessRequestedAccount" in args["body"], "input must not be mutated"


def test_sanitize_passthrough_when_open_scope(open_scope_client):
    args = {
        "body": {
            "accessRequestedAccount": {"advertiserAccountId": "X"},
        }
    }
    out = open_scope_client._sanitize_arguments(args)
    assert out["body"]["accessRequestedAccount"] == {"advertiserAccountId": "X"}


# ── create_campaign_report ─────────────────────────────────────────────


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.mark.anyio
async def test_create_campaign_report_omits_access_when_fixed_scope(fixed_scope_client):
    captured: dict = {}

    async def fake_call_tool(name: str, arguments: dict):
        captured["name"] = name
        captured["arguments"] = arguments
        return {"success": [{"report": {"reportId": "abc-123", "status": "PENDING"}}]}

    with patch.object(fixed_scope_client, "call_tool", side_effect=fake_call_tool):
        await fixed_scope_client.create_campaign_report(
            {
                "reports": [
                    {
                        "format": "GZIP_JSON",
                        "periods": [{"datePeriod": {"startDate": "2026-04-01", "endDate": "2026-04-01"}}],
                    }
                ]
            },
            advertiser_account_id="ENTITY999",
        )

    assert captured["name"] == "reporting-create_campaign_report"
    assert "accessRequestedAccounts" not in captured["arguments"]["body"]


@pytest.mark.anyio
async def test_create_campaign_report_attaches_access_when_open_scope(open_scope_client):
    captured: dict = {}

    async def fake_call_tool(name: str, arguments: dict):
        captured["arguments"] = arguments
        return {"success": [{"report": {"reportId": "abc-123", "status": "PENDING"}}]}

    with patch.object(open_scope_client, "call_tool", side_effect=fake_call_tool):
        await open_scope_client.create_campaign_report(
            {
                "reports": [
                    {
                        "format": "GZIP_JSON",
                        "periods": [{"datePeriod": {"startDate": "2026-04-01", "endDate": "2026-04-01"}}],
                    }
                ]
            },
            advertiser_account_id="ENTITY999",
        )

    body = captured["arguments"]["body"]
    assert body["accessRequestedAccounts"] == [{"advertiserAccountId": "ENTITY999"}]


# ── _parse_result ──────────────────────────────────────────────────────


def _content_result(text: str):
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def test_parse_result_returns_dict_for_valid_json():
    payload = {"campaigns": [{"campaignId": "1"}]}
    out = AmazonAdsMCP._parse_result(_content_result(json.dumps(payload)))
    assert out == payload


def test_parse_result_raises_on_fixed_scope_error_string():
    text = "Cannot pass accessRequestedAccounts in body when using fixed account scope headers"
    with pytest.raises(MCPError):
        AmazonAdsMCP._parse_result(_content_result(text))


def test_parse_result_raises_on_validation_error_string():
    text = "Validation failed: missing required field 'reports'."
    with pytest.raises(MCPError):
        AmazonAdsMCP._parse_result(_content_result(text))


def test_parse_result_returns_text_for_unknown_prose():
    text = "Operation accepted; report queued."
    out = AmazonAdsMCP._parse_result(_content_result(text))
    assert out == {"result": text}


def test_looks_like_server_error_detects_known_markers():
    assert AmazonAdsMCP._looks_like_server_error_text(
        "Cannot pass accessRequestedAccounts in body when using fixed account scope headers"
    )
    assert AmazonAdsMCP._looks_like_server_error_text("Validation error: bad input")
    assert AmazonAdsMCP._looks_like_server_error_text("Forbidden")
    assert not AmazonAdsMCP._looks_like_server_error_text("")
    assert not AmazonAdsMCP._looks_like_server_error_text("{\"campaigns\": []}")
    assert not AmazonAdsMCP._looks_like_server_error_text("Report queued for processing.")
