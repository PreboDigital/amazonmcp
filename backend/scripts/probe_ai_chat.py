#!/usr/bin/env python3
"""Live probe: hit the configured AI provider with the new SYSTEM_PROMPT
and a tiny synthetic context, then run the response through the validator.

Read-only: no DB writes, no MCP calls. Uses the real OpenAI key so it
costs a few cents.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> int:
    from app.config import get_settings
    from app.services import ai_action_validator as validator
    from app.services.ai_service import create_ai_service

    settings = get_settings()
    if not settings.openai_api_key:
        print("OPENAI_API_KEY not configured.")
        return 1

    ai = create_ai_service(
        model_id=f"openai:{settings.openai_model}",
        openai_api_key=settings.openai_api_key,
    )

    fake_context = {
        "account": {
            "name": "Probe Account",
            "region": "na",
            "marketplace": "US",
            "profile_id": "prof-1",
            "account_type": "marketplace",
        },
        "data_freshness": {
            "last_campaign_sync_at": "2026-04-25T07:00:00",
            "last_campaign_sync_days_ago": 3,
            "last_performance_date": "2026-04-23",
            "campaigns_cached": 2,
            "targets_cached": 4,
        },
        "campaigns_summary": {
            "total": 2, "active": 2, "paused": 0,
            "total_spend": 540.0, "total_sales": 1200.0,
            "total_clicks": 320, "total_impressions": 22000, "total_orders": 38,
            "avg_acos": 45.0, "avg_ctr": 1.45, "avg_cpc": 1.69, "avg_cvr": 11.9,
        },
        "all_campaigns": [
            {
                "id": "camp-1", "name": "Auto SP — Brand", "type": "SP",
                "state": "ENABLED", "targeting": "AUTO", "budget": 50,
                "spend": 320, "sales": 800, "acos": 40, "clicks": 220,
                "orders": 25, "impressions": 14000, "ctr": 1.6, "cpc": 1.45, "cvr": 11.4,
                "start_date": "2026-03-01",
            },
            {
                "id": "camp-2", "name": "Manual SP — Generic", "type": "SP",
                "state": "ENABLED", "targeting": "MANUAL", "budget": 30,
                "spend": 220, "sales": 400, "acos": 55, "clicks": 100,
                "orders": 13, "impressions": 8000, "ctr": 1.25, "cpc": 2.20, "cvr": 13.0,
                "start_date": "2026-03-15",
            },
        ],
        "targets_summary": {
            "total": 4,
            "by_type": {"keyword": 4},
            "by_state": {"enabled": 4},
            "by_match_type": {"BROAD": 1, "PHRASE": 1, "EXACT": 2},
            "top_spenders": [
                {
                    "id": "tgt-1", "keyword": "wireless headphones",
                    "match_type": "BROAD", "state": "enabled", "bid": 1.50,
                    "spend": 120, "sales": 90, "acos": 133.3,
                    "clicks": 60, "orders": 2, "impressions": 4000,
                    "campaign_name": "Manual SP — Generic",
                },
                {
                    "id": "tgt-2", "keyword": "noise cancelling earbuds",
                    "match_type": "EXACT", "state": "enabled", "bid": 0.75,
                    "spend": 60, "sales": 280, "acos": 21.4,
                    "clicks": 40, "orders": 8, "impressions": 2400,
                    "campaign_name": "Manual SP — Generic",
                },
            ],
            "non_converting": [
                {
                    "id": "tgt-3", "keyword": "discount audio",
                    "match_type": "BROAD", "state": "enabled", "bid": 1.20,
                    "spend": 40, "clicks": 25, "orders": 0, "impressions": 1500,
                    "campaign_name": "Manual SP — Generic",
                },
            ],
        },
    }

    prompt = (
        "Walk me through the worst-performing keyword in this account and "
        "propose one specific bid change you would make. Use the actual ID "
        "from the data, then emit an ACTIONS block."
    )

    print("=" * 72)
    print("Live AI probe: model =", ai.model, "provider =", ai.provider)
    print("=" * 72)
    print("USER:", prompt)
    print()

    result = await ai.chat(
        user_message=prompt,
        conversation_history=[],
        account_context=fake_context,
    )

    print("ASSISTANT MESSAGE:")
    print("-" * 72)
    print(result["message"][:2000])
    print("-" * 72)
    print(f"\nParsed actions: {len(result['actions'])}")
    for i, a in enumerate(result["actions"]):
        print(f"  [{i}] scope={a.get('scope')} tool={a.get('tool')} "
              f"label={a.get('label')!r}")
        print(f"      args = {json.dumps(a.get('arguments'), default=str)[:240]}")

    if not result["actions"]:
        print("\n(no actions emitted)")
        return 0

    # Run through the validator with a fully-populated DB stub
    cred = SimpleNamespace(id=uuid.uuid4(), profile_id="prof-1")
    # Pretend every entity exists so the validator doesn't reject on ID lookup
    # (we don't want to hit the real DB for this probe)
    import unittest.mock as mock
    with mock.patch.object(validator, "_target_exists", AsyncMock(return_value=True)), \
         mock.patch.object(validator, "_campaign_exists", AsyncMock(return_value=True)), \
         mock.patch.object(validator, "_ad_group_exists", AsyncMock(return_value=True)), \
         mock.patch.object(validator, "_ad_exists", AsyncMock(return_value=True)):
        accepted, rejected = await validator.validate_ai_actions(
            result["actions"],
            db=AsyncMock(name="db"),
            cred=cred,
        )

    print(f"\nValidator: {len(accepted)} accepted, {len(rejected)} rejected")
    for r in rejected:
        print(f"  REJECTED tool={r['tool']!r} error={r['error']}")
    for a in accepted:
        print(f"  ACCEPTED tool={a['tool']!r} args={json.dumps(a['arguments'], default=str)[:240]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
