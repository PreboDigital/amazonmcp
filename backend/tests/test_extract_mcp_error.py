"""Tests for ``app.utils.extract_mcp_error``.

Amazon SP/SB v2 mutation endpoints frequently return HTTP 200 with
per-row failures buried inside the response. The most common shapes:

* ``{"targets": {"success": [...], "error": [...]}}`` — entity bucket
* ``{"successResults": [...], "errorResults": [...]}`` — top level
* ``{"error": "string"}`` / ``{"errors": [...]}``

Without surfacing those, the apply path would mark a change as
"applied" while every keyword Amazon was asked to create actually
failed. These tests pin the detector down to those exact shapes.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.utils import extract_mcp_error  # noqa: E402


def test_returns_none_for_clean_response():
    assert extract_mcp_error({"targets": {"success": [{"targetId": "1"}]}}) is None
    assert extract_mcp_error({"campaigns": []}) is None
    assert extract_mcp_error({}) is None
    assert extract_mcp_error(None) is None


def test_top_level_error_string():
    err = extract_mcp_error({"error": "INVALID_BID"})
    assert err == "INVALID_BID"


def test_top_level_errors_list():
    err = extract_mcp_error({"errors": [{"code": "X"}]})
    assert err and "X" in err


def test_top_level_status_failed():
    err = extract_mcp_error({"status": "FAILED", "message": "bid below minimum"})
    assert err == "bid below minimum"


def test_per_entity_error_bucket_targets():
    """SP v2 multi-status — top-level 200 with all-rows in targets.error."""
    payload = {
        "targets": {
            "success": [],
            "error": [{"index": 0, "errors": [{"errorType": "BID_TOO_LOW"}]}],
        }
    }
    err = extract_mcp_error(payload)
    assert err is not None
    assert "BID_TOO_LOW" in err


def test_per_entity_error_bucket_campaigns():
    payload = {"campaigns": {"error": [{"errorType": "DUPLICATE_NAME"}]}}
    err = extract_mcp_error(payload)
    assert err is not None
    assert "DUPLICATE_NAME" in err


def test_per_entity_errorResults_bucket():
    payload = {"adGroups": {"errorResults": [{"errorType": "BAD_REQUEST"}]}}
    err = extract_mcp_error(payload)
    assert err is not None
    assert "BAD_REQUEST" in err


def test_top_level_errorResults():
    payload = {
        "successResults": [],
        "errorResults": [{"errorType": "INVALID_TARGETING"}],
    }
    err = extract_mcp_error(payload)
    assert err is not None
    assert "INVALID_TARGETING" in err


def test_nested_recursion_still_works():
    """Existing recursion through ``result``/``data``/``items`` still finds errors."""
    payload = {
        "data": {
            "result": {
                "errors": ["AUTH_EXPIRED"],
            }
        }
    }
    err = extract_mcp_error(payload)
    assert err is not None
    assert "AUTH_EXPIRED" in err


def test_string_carrying_error_keyword():
    assert extract_mcp_error("operation failed: timeout") is not None
    assert extract_mcp_error("OK") is None
