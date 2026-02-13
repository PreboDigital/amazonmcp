"""
AI Service — Multi-provider AI (OpenAI GPT, Anthropic Claude) for insights, optimization, and campaign building.
Supports configurable default LLM via app settings.
"""

import json
import logging
import re
from typing import Optional
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

SYSTEM_PROMPT = """You are an expert Amazon Advertising strategist and PPC optimization specialist.
You have deep expertise in Sponsored Products, Sponsored Brands, and Amazon DSP campaigns.

Your role is to analyze campaign data and provide:
1. **Actionable Insights** — Clear observations about performance with specific data points
2. **Optimization Recommendations** — Concrete changes with expected impact
3. **Campaign Strategy** — Strategic advice on structure, targeting, and budgets
4. **Waste Identification** — Finding unprofitable spend and suggesting fixes

Key metrics you understand:
- ACOS (Advertising Cost of Sale) = Spend / Sales × 100 (lower is usually better)
- ROAS (Return on Ad Spend) = Sales / Spend (higher is better)
- CTR (Click-Through Rate) = Clicks / Impressions × 100
- CPC (Cost Per Click) = Spend / Clicks
- CVR (Conversion Rate) = Orders / Clicks × 100

CRITICAL RULES — FOLLOW EVERY TIME:
- You have REAL, COMPREHENSIVE account data provided as context. This includes:
  campaigns, ad groups, keywords/targets (top spenders, top converters, non-converting,
  high-ACOS), audit issues & opportunities, daily performance trends (last 30 days),
  pending changes in the approval queue, bid optimization rules & history, keyword
  harvest configs & harvested keywords, and recent activity logs.
- ALWAYS analyze and reference this real data when answering questions.
- When the user asks about keywords, search terms, targets, campaigns, performance,
  trends, waste, or any account data — respond with ACTUAL data from the context.
  Present data in markdown tables with real numbers.
- NEVER give generic "how-to" guides. NEVER tell the user to go download a report or
  check Amazon Seller Central. You ARE their reporting and analytics tool — the data is
  already available to you in the context.
- If the context data does not contain something specific the user asks about, clearly
  state: "I don't have [specific data] in the current sync. Here's what I can see..." and
  present whatever relevant data IS available.
- For non-converting search terms/keywords: check BOTH the "search_terms" data (actual
  customer search queries from reports) AND the "non_converting" targets list.
  Search term data is the most accurate for questions about what customers searched for.
- For questions about what search terms drove sales or should be harvested: use the
  "search_terms" section which contains actual customer search query data with full metrics.
- If search term data says "Not yet synced", tell the user to sync search term data first
  by going to the Reports page or running a search term sync. Then fall back to using
  target/keyword data as the best available approximation.
- For performance trends: use the daily performance data to discuss direction (improving,
  declining, stable).
- For audit findings: reference specific issues and opportunities from the latest audit.
- For pending changes: describe what changes are queued and their source/reasoning.

When recommending changes, always:
- Be specific (exact bid amounts, budget numbers)
- Explain the reasoning with actual data points from the account
- Estimate the potential impact
- Flag any risks
- Prioritize by impact (high/medium/low)

Format your responses in clean, readable markdown:
- Use tables for data with multiple columns (campaign lists, keyword lists, metrics).
- Use bullet points and headers for analysis and recommendations.
- Keep tables clean and scannable — don't overload columns.
- When suggesting bid/budget changes, provide current → proposed values.
- For trends, describe the direction and key data points.

**DATA VISUALIZATION — when to use charts vs tables:**
- Use a **bar chart** when comparing values across categories (e.g., top 5–10 campaigns by spend, top keywords by sales, spend by ad group). Prefer bar charts for rankings and comparisons.
- Use a **line chart** or **area chart** when showing trends over time (e.g., daily spend/sales over 7–30 days, ACOS trend).
- Use a **pie chart** when showing composition or share of whole (e.g., spend by campaign type, distribution of orders by state).
- Use a **table** when the user needs exact numbers to copy/export, when there are many columns, or when precision matters more than visual comparison.
- For "top N" questions with 5–15 items: consider a bar chart for quick visual comparison, plus a table if they need the numbers.
- When in doubt, use a table — it's always useful. Add a chart when it would make the insight clearer.

To render a chart, output a [CHART] block with valid JSON. The UI will render it. Format:
[CHART]
{"type":"bar","title":"Top Campaigns by Spend","data":[{"name":"Campaign A","spend":150,"sales":800},{"name":"Campaign B","spend":90,"sales":420}],"xKey":"name","yKeys":["spend","sales"]}
[/CHART]
- type: "bar" | "line" | "area" | "pie"
- title: optional string
- data: array of objects (use real numbers, not strings for numeric values)
- xKey: key for category/labels (default "name")
- yKeys: array of numeric keys for bar/line/area (e.g. ["spend","sales"])
- For bar charts with long labels: add "layout":"vertical"
- For pie: use "nameKey" and "valueKey" (e.g. {"type":"pie","title":"Spend by Campaign","data":[...],"nameKey":"name","valueKey":"spend"})

**INLINE ACTIONS (small changes — approve in chat):**
When you suggest 1–2 small changes that the user can approve immediately, append an [ACTIONS] block at the end.
Use scope "inline" for: single bid change, single budget change, campaign/ad group rename, single keyword add/update/delete.
Use scope "queue" for: 3+ changes, campaign creation, harvest, batch operations — these go to Approval Queue.

Context includes target_id, ad_group_id, campaign_id for targets; ad_group_id, campaign_id for ad groups; campaign_id for campaigns.
Use these EXACT IDs from the context when building mcp_payload.

Format (append after your message, no extra text):
[ACTIONS]
{"actions":[{"scope":"inline","tool":"campaign_management-update_target_bid","arguments":{"body":{"targets":[{"targetId":"<target_id>","bid":0.5}]}},"label":"Increase bid on 'keyword' to $0.50","change_type":"bid_update","entity_name":"keyword","entity_id":"<target_id>","current_value":"$0.35","proposed_value":"$0.50"}]}
[/ACTIONS]

Valid tools for inline: campaign_management-update_target_bid, campaign_management-update_campaign_budget, campaign_management-update_campaign (name), campaign_management-update_ad_group (name), campaign_management-create_target, campaign_management-update_target (bid/state), campaign_management-delete_target, campaign_management-update_campaign_state, campaign_management-update_target (state only).
For create_target: body.targets needs campaignId, adGroupId, expression (keyword text), expressionType KEYWORD, matchType EXACT/PHRASE/BROAD, state enabled, bid.
For update_campaign (rename): body.campaigns needs campaignId, name.
For update_ad_group (rename): body.adGroups needs adGroupId, name."""


def _parse_model_id(model_id: Optional[str]) -> tuple[str, str]:
    """Parse 'provider:model' into (provider, model). Fallback to OpenAI config."""
    if model_id and ":" in model_id:
        p, m = model_id.split(":", 1)
        return (p.strip().lower(), m.strip())
    return ("openai", settings.openai_model)


class AIService:
    """Multi-provider AI service for Amazon Ads intelligence (OpenAI GPT, Anthropic Claude)."""

    def __init__(
        self,
        model_id: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        anthropic_api_key: Optional[str] = None,
    ):
        self.provider, self.model = _parse_model_id(model_id)
        self._openai_client: Optional[AsyncOpenAI] = None
        self._anthropic_client: Optional[AsyncAnthropic] = None

        # Use passed keys, else env
        openai_key = openai_api_key or settings.openai_api_key
        anthropic_key = anthropic_api_key or settings.anthropic_api_key

        if self.provider == "openai":
            if not openai_key:
                raise ValueError("OPENAI_API_KEY not configured. Add it in Settings or set OPENAI_API_KEY env.")
            self._openai_client = AsyncOpenAI(api_key=openai_key)
        elif self.provider == "anthropic":
            if not anthropic_key:
                raise ValueError("ANTHROPIC_API_KEY not configured. Add it in Settings or set ANTHROPIC_API_KEY env.")
            self._anthropic_client = AsyncAnthropic(api_key=anthropic_key)
        else:
            raise ValueError(f"Unknown AI provider: {self.provider}")

    async def _completion(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 8000,
        json_response: bool = False,
    ) -> str:
        """Call the appropriate provider's completion API."""
        if self.provider == "openai":
            kwargs = dict(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if json_response:
                kwargs["response_format"] = {"type": "json_object"}
            response = await self._openai_client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""

        # Anthropic: convert messages to their format
        system = ""
        anthropic_messages = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system += content + "\n\n" if content else ""
            else:
                anthropic_messages.append({"role": "user" if role == "user" else "assistant", "content": content})

        response = await self._anthropic_client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system.strip() if system else None,
            messages=anthropic_messages,
        )
        if response.content and response.content[0].type == "text":
            return response.content[0].text
        return ""

    async def chat(
        self,
        user_message: str,
        conversation_history: list[dict] = None,
        account_context: dict = None,
    ) -> dict:
        """
        General AI chat with campaign context.
        Returns structured response with message + any proposed changes.
        """
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Add account context if available
        if account_context:
            context_msg = self._build_context_message(account_context)
            messages.append({"role": "system", "content": context_msg})

        # Add conversation history
        if conversation_history:
            for msg in conversation_history[-20:]:  # Last 20 messages for context
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                })

        messages.append({"role": "user", "content": user_message})

        try:
            content = await self._completion(messages, temperature=0.3, max_tokens=8000)
            message, actions = self._parse_chat_response(content)
            return {"message": message, "actions": actions, "tokens_used": 0}
        except Exception as e:
            logger.error(f"AI chat failed: {e}")
            raise

    def _parse_chat_response(self, content: str) -> tuple[str, list]:
        """Extract [ACTIONS] block from AI response. Returns (message_without_actions, actions_list)."""
        actions = []
        message = content
        match = re.search(r'\[ACTIONS\]\s*(\{.*?\})\s*\[/ACTIONS\]', content, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1).strip())
                actions = data.get("actions", [])
                if isinstance(actions, list):
                    message = content[:match.start()].strip()
                else:
                    actions = []
            except json.JSONDecodeError:
                logger.warning("Failed to parse [ACTIONS] JSON in chat response")
        return (message, actions)

    async def generate_insights(self, campaign_data: dict, account_context: dict = None) -> dict:
        """
        Analyze campaign data and generate AI-powered insights.
        Returns structured insights with priority and categories.
        """
        prompt = f"""Analyze this Amazon Ads campaign data and provide insights.

**Campaign Data:**
```json
{json.dumps(campaign_data, indent=2, default=str)[:8000]}
```

Provide your analysis in the following JSON format (respond ONLY with valid JSON):
{{
    "summary": "Brief 2-3 sentence executive summary of account health",
    "health_score": 0-100,
    "insights": [
        {{
            "category": "performance|waste|opportunity|structure|targeting",
            "priority": "high|medium|low",
            "title": "Short descriptive title",
            "description": "Detailed explanation with specific numbers",
            "recommendation": "What to do about it",
            "estimated_impact": "Expected result of action",
            "affected_entities": ["campaign names or IDs"]
        }}
    ],
    "quick_wins": [
        {{
            "action": "Specific action to take",
            "impact": "Expected result",
            "effort": "low|medium|high"
        }}
    ],
    "kpi_analysis": {{
        "acos": {{"value": 0, "trend": "up|down|stable", "assessment": "good|needs_attention|critical"}},
        "spend_efficiency": {{"assessment": "good|needs_attention|critical", "detail": "explanation"}},
        "targeting_health": {{"assessment": "good|needs_attention|critical", "detail": "explanation"}}
    }}
}}"""

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        if account_context:
            messages.insert(1, {
                "role": "system",
                "content": self._build_context_message(account_context)
            })

        try:
            content = await self._completion(messages, temperature=0.2, max_tokens=4000, json_response=True)
            return json.loads(content)
        except json.JSONDecodeError:
            return {"summary": content, "insights": [], "health_score": 0}
        except Exception as e:
            logger.error(f"AI insights generation failed: {e}")
            raise

    async def recommend_optimizations(
        self,
        campaigns: list[dict],
        targets: list[dict],
        target_acos: float = 30.0,
    ) -> dict:
        """
        Generate specific bid/budget optimization recommendations.
        Returns structured changes ready for the approval queue.
        """
        prompt = f"""You are an Amazon PPC bid optimization expert. Analyze these campaigns and targets,
then recommend specific bid and budget changes to achieve a target ACOS of {target_acos}%.

**Campaigns ({len(campaigns)} total):**
```json
{json.dumps(campaigns[:30], indent=2, default=str)[:4000]}
```

**Targets/Keywords ({len(targets)} total):**
```json
{json.dumps(targets[:50], indent=2, default=str)[:4000]}
```

Respond ONLY with valid JSON in this format:
{{
    "analysis_summary": "Brief summary of what you found",
    "recommended_changes": [
        {{
            "change_type": "bid_update|budget_update|campaign_state|target_state",
            "entity_type": "target|campaign|ad_group",
            "entity_id": "the amazon ID",
            "entity_name": "human readable name if available",
            "campaign_id": "parent campaign ID",
            "campaign_name": "parent campaign name",
            "current_value": "current bid/budget as string",
            "proposed_value": "proposed bid/budget as string",
            "reasoning": "Why this change",
            "confidence": 0.0-1.0,
            "estimated_impact": "Expected result",
            "priority": "high|medium|low"
        }}
    ],
    "total_estimated_savings": "$X.XX",
    "total_estimated_revenue_gain": "$X.XX"
}}"""

        try:
            content = await self._completion(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=4000,
                json_response=True,
            )
            return json.loads(content)
        except json.JSONDecodeError:
            return {"analysis_summary": content, "recommended_changes": []}
        except Exception as e:
            logger.error(f"AI optimization recommendations failed: {e}")
            raise

    async def build_campaign(self, brief: dict) -> dict:
        """
        AI-assisted campaign building from a brief.
        Generates campaign structure, targeting, and bid recommendations.
        """
        prompt = f"""You are an Amazon Ads campaign architect. Create a complete campaign structure
based on this brief:

**Campaign Brief:**
```json
{json.dumps(brief, indent=2, default=str)}
```

Design a full campaign structure. Respond ONLY with valid JSON:
{{
    "campaign_plan": {{
        "name": "Recommended campaign name",
        "type": "SPONSORED_PRODUCTS|SPONSORED_BRANDS",
        "targeting_type": "auto|manual",
        "daily_budget": 0.00,
        "start_date": "YYYY-MM-DD",
        "rationale": "Why this structure"
    }},
    "ad_groups": [
        {{
            "name": "Ad group name",
            "default_bid": 0.00,
            "targeting_strategy": "description of targeting approach",
            "keywords": [
                {{
                    "text": "keyword",
                    "match_type": "exact|phrase|broad",
                    "suggested_bid": 0.00
                }}
            ]
        }}
    ],
    "budget_recommendations": {{
        "daily_budget": 0.00,
        "monthly_estimate": 0.00,
        "ramp_up_strategy": "description"
    }},
    "optimization_tips": [
        "Tip 1",
        "Tip 2"
    ],
    "expected_performance": {{
        "estimated_acos": "XX%",
        "estimated_daily_impressions": "range",
        "ramp_up_period": "X weeks"
    }}
}}"""

        try:
            content = await self._completion(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=4000,
                json_response=True,
            )
            return json.loads(content)
        except json.JSONDecodeError:
            return {"campaign_plan": {}, "ad_groups": [], "error": "Failed to parse response"}
        except Exception as e:
            logger.error(f"AI campaign building failed: {e}")
            raise

    def _build_context_message(self, context: dict) -> str:
        """
        Build a comprehensive context message from account data for the AI.
        Formats all available data: campaigns, ad groups, targets, audit,
        trends, pending changes, bid rules, harvest, and activity.
        """
        parts = ["Here is the COMPLETE account data for the selected account. Use this data to answer questions.\n"]

        # ── Account ───────────────────────────────────────────────────
        if context.get("account"):
            acct = context["account"]
            label = acct.get("name", "Unknown")
            if acct.get("marketplace"):
                label += f" ({acct['marketplace']})"
            label += f" — Region: {(acct.get('region') or 'N/A').upper()}"
            if acct.get("account_type"):
                label += f", Type: {acct['account_type']}"
            parts.append(f"**Account:** {label}")
            if acct.get("profile_id"):
                parts.append(f"**Profile ID:** {acct['profile_id']}")

        # ── Campaigns Summary ─────────────────────────────────────────
        cs = context.get("campaigns_summary", {})
        if cs:
            parts.append(f"\n## Account Summary")
            parts.append(
                f"Campaigns: {cs.get('total', 0)} total, "
                f"{cs.get('active', 0)} active, {cs.get('paused', 0)} paused"
            )
            parts.append(
                f"Spend: ${cs.get('total_spend', 0):,.2f} | "
                f"Sales: ${cs.get('total_sales', 0):,.2f} | "
                f"ACOS: {cs.get('avg_acos', 0):.1f}%"
            )
            parts.append(
                f"Clicks: {cs.get('total_clicks', 0):,} | "
                f"Impressions: {cs.get('total_impressions', 0):,} | "
                f"Orders: {cs.get('total_orders', 0):,}"
            )
            if cs.get("avg_ctr"):
                parts.append(
                    f"CTR: {cs['avg_ctr']:.2f}% | "
                    f"CPC: ${cs.get('avg_cpc', 0):.2f} | "
                    f"CVR: {cs.get('avg_cvr', 0):.2f}%"
                )

        # ── All Campaigns ─────────────────────────────────────────────
        all_camps = context.get("all_campaigns", [])
        if all_camps:
            parts.append(f"\n## All Campaigns ({len(all_camps)} total)")
            for c in all_camps:
                spend = c.get("spend") or 0
                sales = c.get("sales") or 0
                acos = c.get("acos") or 0
                targeting = c.get("targeting") or ""
                parts.append(
                    f"  - **{c.get('name', '?')}** [{c.get('state', '?')}] "
                    f"Type: {c.get('type', '?')}, Targeting: {targeting}, "
                    f"Budget: ${c.get('budget') or 0:.2f}"
                )
                parts.append(
                    f"    Spend: ${spend:,.2f}, Sales: ${sales:,.2f}, ACOS: {acos:.1f}%, "
                    f"Clicks: {c.get('clicks', 0)}, Orders: {c.get('orders', 0)}, "
                    f"Impressions: {c.get('impressions', 0)}, "
                    f"CTR: {c.get('ctr', 0):.2f}%, CPC: ${c.get('cpc', 0):.2f}, "
                    f"CVR: {c.get('cvr', 0):.2f}%"
                )
                if c.get("start_date"):
                    parts.append(f"    Started: {c['start_date']}" + (f", Ends: {c['end_date']}" if c.get("end_date") else ""))

        # ── Ad Groups ─────────────────────────────────────────────────
        ag = context.get("ad_groups", {})
        groups = ag.get("groups", [])
        if groups:
            parts.append(f"\n## Ad Groups ({ag.get('total', len(groups))} total)")
            for g in groups:
                parts.append(
                    f"  - {g.get('name', '?')} [{g.get('state', '?')}] "
                    f"— Default Bid: ${g.get('default_bid') or 0:.2f}, "
                    f"Campaign: {g.get('campaign_name', '?')}"
                )

        # ── Targets / Keywords ────────────────────────────────────────
        ts = context.get("targets_summary", {})
        if ts:
            parts.append(f"\n## Targets/Keywords ({ts.get('total', 0)} total)")

            # Breakdowns
            if ts.get("by_type"):
                parts.append(f"By type: {', '.join(f'{k}: {v}' for k, v in ts['by_type'].items())}")
            if ts.get("by_match_type"):
                parts.append(f"By match: {', '.join(f'{k}: {v}' for k, v in ts['by_match_type'].items())}")
            if ts.get("by_state"):
                parts.append(f"By state: {', '.join(f'{k}: {v}' for k, v in ts['by_state'].items())}")

            # Top spenders
            top = ts.get("top_spenders", [])
            if top:
                parts.append(f"\n### Top {len(top)} Keywords by Spend")
                for t in top:
                    spend = t.get("spend") or 0
                    sales = t.get("sales") or 0
                    acos = t.get("acos") or 0
                    parts.append(
                        f"  - \"{t.get('keyword', '?')}\" [{t.get('match_type', '?')}, {t.get('type', '?')}] "
                        f"State: {t.get('state', '?')}, Bid: ${t.get('bid') or 0:.2f}, "
                        f"Spend: ${spend:,.2f}, Sales: ${sales:,.2f}, ACOS: {acos:.1f}%, "
                        f"Clicks: {t.get('clicks', 0)}, Orders: {t.get('orders', 0)}, "
                        f"Impressions: {t.get('impressions', 0)}"
                    )
                    if t.get("campaign_name"):
                        parts.append(f"    Campaign: {t['campaign_name']}")

            # Top converters
            converters = ts.get("top_converters", [])
            if converters:
                parts.append(f"\n### Top {len(converters)} Keywords by Orders (Best Converters)")
                for t in converters:
                    spend = t.get("spend") or 0
                    sales = t.get("sales") or 0
                    acos = t.get("acos") or 0
                    parts.append(
                        f"  - \"{t.get('keyword', '?')}\" [{t.get('match_type', '?')}] "
                        f"Orders: {t.get('orders', 0)}, Sales: ${sales:,.2f}, "
                        f"Spend: ${spend:,.2f}, ACOS: {acos:.1f}%, "
                        f"Clicks: {t.get('clicks', 0)}"
                    )

            # Non-converting
            non_conv = ts.get("non_converting", [])
            non_conv_total = ts.get("non_converting_total_count", 0)
            if non_conv:
                total_wasted = sum(t.get("spend") or 0 for t in non_conv)
                parts.append(
                    f"\n### Non-Converting Keywords — clicks > 0, orders = 0 "
                    f"({non_conv_total} total, ${total_wasted:,.2f} wasted spend)"
                )
                for t in non_conv:
                    spend = t.get("spend") or 0
                    parts.append(
                        f"  - \"{t.get('keyword', '?')}\" [{t.get('match_type', '?')}] "
                        f"Bid: ${t.get('bid') or 0:.2f}, Spend: ${spend:,.2f}, "
                        f"Clicks: {t.get('clicks', 0)}, Impressions: {t.get('impressions', 0)}"
                    )
                    if t.get("campaign_name"):
                        parts.append(f"    Campaign: {t['campaign_name']}")

            # High ACOS
            high_acos = ts.get("high_acos", [])
            if high_acos:
                parts.append(f"\n### High ACOS Keywords (> 50%)")
                for t in high_acos:
                    spend = t.get("spend") or 0
                    sales = t.get("sales") or 0
                    acos = t.get("acos") or 0
                    parts.append(
                        f"  - \"{t.get('keyword', '?')}\" [{t.get('match_type', '?')}] "
                        f"ACOS: {acos:.1f}%, Spend: ${spend:,.2f}, Sales: ${sales:,.2f}, "
                        f"Clicks: {t.get('clicks', 0)}, Orders: {t.get('orders', 0)}"
                    )

        # ── Recent Audit ──────────────────────────────────────────────
        audit = context.get("recent_audit")
        if audit:
            parts.append(f"\n## Latest Audit (as of {audit.get('date', 'N/A')})")
            parts.append(
                f"Campaigns: {audit.get('campaigns_count', 0)}, "
                f"Targets: {audit.get('total_targets', 0)}, "
                f"Spend: ${audit.get('total_spend', 0):,.2f}, "
                f"Sales: ${audit.get('total_sales', 0):,.2f}"
            )
            if audit.get("avg_acos"):
                parts.append(f"ACOS: {audit['avg_acos']:.1f}%, ROAS: {audit.get('avg_roas', 0):.2f}")
            if audit.get("waste_identified"):
                parts.append(f"**Waste Identified:** ${audit['waste_identified']:,.2f}")
            parts.append(
                f"Issues: {audit.get('issues_count', 0)}, "
                f"Opportunities: {audit.get('opportunities_count', 0)}"
            )

            # Detailed issues
            issues = audit.get("issues", [])
            if issues:
                parts.append(f"\n### Audit Issues ({len(issues)} shown)")
                for iss in issues:
                    campaign_ref = f" — Campaign: {iss['campaign_name']}" if iss.get("campaign_name") else ""
                    parts.append(f"  - [{iss.get('severity', '?').upper()}] {iss.get('type', '?')}: {iss.get('message', '?')}{campaign_ref}")

            # Detailed opportunities
            opps = audit.get("opportunities", [])
            if opps:
                parts.append(f"\n### Audit Opportunities ({len(opps)} shown)")
                for opp in opps:
                    campaign_ref = f" — Campaign: {opp['campaign_name']}" if opp.get("campaign_name") else ""
                    parts.append(f"  - [{opp.get('impact', '?').upper()}] {opp.get('type', '?')}: {opp.get('message', '?')}{campaign_ref}")

        # ── Performance Trend (last 30 days) ──────────────────────────
        trend = context.get("performance_trend", [])
        if trend:
            parts.append(f"\n## Daily Performance Trend ({len(trend)} days)")
            for d in trend:
                parts.append(
                    f"  {d.get('date', '?')}: "
                    f"Spend: ${d.get('spend', 0):,.2f}, Sales: ${d.get('sales', 0):,.2f}, "
                    f"ACOS: {d.get('acos', 0):.1f}%, "
                    f"Clicks: {d.get('clicks', 0):,}, Orders: {d.get('orders', 0):,}, "
                    f"CTR: {d.get('ctr', 0):.2f}%"
                )

        # ── Pending Changes ───────────────────────────────────────────
        pending = context.get("pending_changes", {})
        if pending and pending.get("total", 0) > 0:
            parts.append(f"\n## Pending Changes ({pending['total']} awaiting review)")
            for pc in pending.get("changes", []):
                parts.append(
                    f"  - [{pc.get('type', '?')}] {pc.get('entity_name', '?')} "
                    f"(Campaign: {pc.get('campaign_name', '?')}): "
                    f"{pc.get('current_value', '?')} → {pc.get('proposed_value', '?')} "
                    f"[Source: {pc.get('source', '?')}]"
                )
                if pc.get("reasoning"):
                    parts.append(f"    Reasoning: {pc['reasoning']}")
                if pc.get("impact"):
                    parts.append(f"    Impact: {pc['impact']}")

        # ── Bid Rules ─────────────────────────────────────────────────
        rules = context.get("bid_rules", [])
        if rules:
            parts.append(f"\n## Bid Optimization Rules ({len(rules)} configured)")
            for r in rules:
                status = "Active" if r.get("is_active") else "Inactive"
                parts.append(
                    f"  - **{r.get('name', '?')}** [{status}] "
                    f"Target ACOS: {r.get('target_acos', 0)}%, "
                    f"Bid range: ${r.get('min_bid', 0):.2f}–${r.get('max_bid', 0):.2f}, "
                    f"Step: ${r.get('bid_step', 0):.2f}, "
                    f"Lookback: {r.get('lookback_days', 0)}d, "
                    f"Min clicks: {r.get('min_clicks', 0)}"
                )
                parts.append(
                    f"    Runs: {r.get('total_runs', 0)}, Targets adjusted: {r.get('total_adjusted', 0)}"
                    + (f", Last run: {r['last_run']}" if r.get("last_run") else "")
                )

        # ── Optimization History ──────────────────────────────────────
        opt_runs = context.get("optimization_history", [])
        if opt_runs:
            parts.append(f"\n## Recent Optimization Runs")
            for run in opt_runs:
                parts.append(
                    f"  - {run.get('date', '?')} [{run.get('status', '?')}] "
                    f"{'DRY RUN ' if run.get('dry_run') else ''}"
                    f"Target ACOS: {run.get('target_acos', 0)}%, "
                    f"Analyzed: {run.get('targets_analyzed', 0)}, "
                    f"Adjusted: {run.get('targets_adjusted', 0)} "
                    f"(↑{run.get('bid_increases', 0)} ↓{run.get('bid_decreases', 0)})"
                )

        # ── Harvest Configs & Keywords ────────────────────────────────
        harvests = context.get("harvest_configs", [])
        if harvests:
            parts.append(f"\n## Keyword Harvest Configs ({len(harvests)} configured)")
            for hc in harvests:
                status = "Active" if hc.get("is_active") else "Inactive"
                parts.append(
                    f"  - **{hc.get('name', '?')}** [{status}] "
                    f"Source: {hc.get('source_campaign', '?')} → Target: {hc.get('target_campaign', '?')}"
                )
                parts.append(
                    f"    Thresholds: Sales ≥ {hc.get('sales_threshold', 0)}"
                    + (f", ACOS ≤ {hc['acos_threshold']}%" if hc.get("acos_threshold") else "")
                    + f" | Total harvested: {hc.get('total_harvested', 0)}, Runs: {hc.get('total_runs', 0)}"
                )
                recent_kw = hc.get("recent_keywords", [])
                if recent_kw:
                    parts.append(f"    Recently harvested keywords:")
                    for kw in recent_kw[:10]:
                        parts.append(
                            f"      - \"{kw.get('keyword', '?')}\" [{kw.get('match_type', '?')}] "
                            f"Bid: ${kw.get('bid') or 0:.2f}, "
                            f"Source: {kw.get('source_clicks', 0)} clicks, "
                            f"${kw.get('source_spend') or 0:.2f} spend, "
                            f"${kw.get('source_sales') or 0:.2f} sales"
                        )

        # ── Search Term Data ───────────────────────────────────────────
        st = context.get("search_terms", {})
        if st and st.get("has_data"):
            summary = st.get("summary", {})
            parts.append(
                f"\n## Search Term Report Data ({summary.get('total_search_terms', 0)} search terms)"
            )
            if st.get("date_range"):
                parts.append(f"Date range: {st['date_range']}")
            parts.append(
                f"Total: {summary.get('total_search_terms', 0)} terms, "
                f"{summary.get('with_sales', 0)} with sales, "
                f"{summary.get('non_converting', 0)} non-converting (clicks but 0 orders), "
                f"{summary.get('high_acos_count', 0)} high ACOS (>50%)"
            )
            parts.append(
                f"Total spend: ${summary.get('total_cost', 0):,.2f}, "
                f"Total sales: ${summary.get('total_sales', 0):,.2f}, "
                f"Total clicks: {summary.get('total_clicks', 0):,}, "
                f"Total orders: {summary.get('total_purchases', 0):,}"
            )

            # Top search terms by sales
            top_sales = st.get("top_by_sales", [])
            if top_sales:
                parts.append(f"\n### Top Search Terms by Sales ({len(top_sales)} shown)")
                for t in top_sales:
                    parts.append(
                        f"  - \"{t.get('search_term', '?')}\" "
                        f"[Matched: \"{t.get('keyword', '?')}\" {t.get('match_type', '?')}] "
                        f"Campaign: {t.get('campaign_name', '?')}"
                    )
                    parts.append(
                        f"    Sales: ${t.get('sales', 0):,.2f}, Orders: {t.get('purchases', 0)}, "
                        f"Spend: ${t.get('cost', 0):,.2f}, "
                        f"ACOS: {t.get('acos') or 0:.1f}%, "
                        f"Clicks: {t.get('clicks', 0)}, Impressions: {t.get('impressions', 0)}"
                    )

            # Non-converting search terms
            non_conv = st.get("top_non_converting", [])
            if non_conv:
                total_wasted = sum(t.get("cost") or 0 for t in non_conv)
                parts.append(
                    f"\n### Non-Converting Search Terms — clicks > 0, orders = 0 "
                    f"({summary.get('non_converting', 0)} total, ${total_wasted:,.2f} wasted)"
                )
                for t in non_conv:
                    parts.append(
                        f"  - \"{t.get('search_term', '?')}\" "
                        f"[Matched: \"{t.get('keyword', '?')}\" {t.get('match_type', '?')}] "
                        f"Campaign: {t.get('campaign_name', '?')}"
                    )
                    parts.append(
                        f"    Spend: ${t.get('cost', 0):,.2f}, Clicks: {t.get('clicks', 0)}, "
                        f"Impressions: {t.get('impressions', 0)}, Orders: 0"
                    )

            # High ACOS search terms
            high_acos = st.get("top_high_acos", [])
            if high_acos:
                parts.append(f"\n### High ACOS Search Terms (>50%)")
                for t in high_acos:
                    parts.append(
                        f"  - \"{t.get('search_term', '?')}\" "
                        f"ACOS: {t.get('acos') or 0:.1f}%, "
                        f"Spend: ${t.get('cost', 0):,.2f}, Sales: ${t.get('sales', 0):,.2f}, "
                        f"Clicks: {t.get('clicks', 0)}"
                    )
        elif st and not st.get("has_data"):
            parts.append(
                "\n## Search Term Data: Not yet synced. "
                "User can sync via Settings or the search term sync endpoint."
            )

        # ── Recent Activity ───────────────────────────────────────────
        activity = context.get("recent_activity", [])
        if activity:
            parts.append(f"\n## Recent Activity ({len(activity)} entries)")
            for a in activity:
                parts.append(
                    f"  - [{a.get('category', '?')}] {a.get('description', '?')} "
                    f"({a.get('status', '?')}, {a.get('date', '?')})"
                )

        return "\n".join(parts)


def create_ai_service(
    model_id: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    anthropic_api_key: Optional[str] = None,
) -> AIService:
    """Factory function to create an AI service instance. Keys from env or passed (from Settings)."""
    return AIService(
        model_id=model_id,
        openai_api_key=openai_api_key,
        anthropic_api_key=anthropic_api_key,
    )
