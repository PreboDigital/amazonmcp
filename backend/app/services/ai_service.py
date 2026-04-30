"""
AI Service — Multi-provider AI (OpenAI GPT, Anthropic Claude) for insights, optimization, and campaign building.
Supports configurable default LLM via app settings.
"""

import json
import logging
import re
from typing import Any, Optional
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from app.config import get_settings
from app.services.ai_tools import (
    anthropic_tool_specs,
    openai_tool_specs,
    tool_call_to_action,
)
from app.services.ai_read_tools import (
    READ_TOOL_NAMES,
    RESULT_CHAR_CAP,
    anthropic_read_tool_specs,
    openai_read_tool_specs,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Prompt-size budgets ───────────────────────────────────────────────
# Hard caps on what we serialize into a single chat call. Real models tolerate
# more, but we hit cost / latency / accuracy cliffs above ~30k chars of
# context. Tune via env if needed; defaults are conservative.
MAX_CONTEXT_CHARS = 60_000        # full account-data dump cap
MAX_HISTORY_CHARS = 16_000        # rolling conversation history cap
MAX_HISTORY_MESSAGES = 40         # always keep at most this many turns
# Multi-turn tool loop budget. Each hop = 1 OpenAI completion + N read tool
# executions. Mutation tool calls always end the loop (returned as actions).
MAX_TOOL_HOPS = 5
# Per-section row caps inside _build_context_message
SECTION_ROW_CAPS = {
    "all_campaigns": 60,
    "ad_groups": 80,
    "top_spenders": 25,
    "top_converters": 20,
    "non_converting": 25,
    "high_acos": 25,
    "performance_trend": 30,
    "pending_changes": 25,
    "issues": 25,
    "opportunities": 25,
    "bid_rules": 15,
    "optimization_history": 10,
    "harvest_configs": 10,
    "harvested_keywords": 10,
    "search_terms_top": 25,
    "search_terms_non_converting": 25,
    "search_terms_high_acos": 25,
    "recent_activity": 20,
}


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
- For continuity across chats: when the context contains a "Previous Conversations"
  section, treat it as memory of what the user asked in earlier threads on this
  same account. Reference it when the user says things like "what did I ask
  yesterday", "follow up on that audit", "the campaign we discussed last time"
  — but do NOT fabricate details that are not in the recap. If a prior thread
  is relevant but the recap is too thin, ask the user to open that thread.

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

**READ TOOLS — fetch data on demand:**
The context above is a *summary*. For anything beyond that summary
(specific campaigns, full target lists, search terms in any date
range, daily trends, pending changes), call a read tool instead of
guessing or telling the user "I don't have that".

Tool tiers (use the cheapest one that answers the question):
- ``db_query_campaigns`` / ``db_query_ad_groups`` / ``db_query_targets``
  / ``db_query_search_terms`` / ``db_query_performance_trend`` /
  ``db_query_pending_changes`` — instant local cache. Prefer these.
- ``mcp_list_campaigns`` / ``mcp_list_ad_groups`` / ``mcp_list_targets``
  — live Amazon Ads snapshot, ~1-5s. Use only when the user explicitly
  asks for "right now" / "live" data, or when the matching db_* call
  returned 0 rows.
- ``_request_sync`` — when historical data outside the cached window is
  needed (e.g. a month that was never synced). This does NOT fetch in
  this turn; it asks the UI to start a sync.

You may call multiple read tools in a single turn. Use the results to
answer; do not narrate the tool calls themselves to the user.

**MANDATORY LOOKUP RULE — never reply "I couldn't find X":**
If the user references an entity by *name* (campaign, ad group, keyword,
or search term) that is not visible in the context summary, you MUST
call a ``db_query_*`` tool with the matching ``name_search`` /
``keyword_search`` / ``term_search`` parameter BEFORE telling the user
the data is missing. Lookup chain to follow:

  1. Ad group by name → ``db_query_ad_groups(name_search="...")``.
     The result row gives ``id`` (Amazon ad group id), ``campaign_id``,
     and ``defaultBid``.
  2. Keyword/target by text → ``db_query_targets(keyword_search="...",
     ad_group_id=<id from step 1>)``. The result row gives ``id``
     (Amazon target id) and the current ``bid``.
  3. Search term by text → ``db_query_search_terms(term_search="...",
     start_date=<7-day window>, end_date=<today>)``.

Only after these calls return zero rows may you say the data is
missing — and in that case suggest a sync (``_request_sync``) instead
of giving up.

**NEVER infer ad-group names from campaign names.** Campaign names
like ``"[PD] RAM Rugby - Tackle Bag - SP - Product & KW Targeting"``
commonly contain the words ``"keyword"``, ``"product"``,
``"targeting"`` etc. These describe the *campaign*, not its ad
groups. The same campaign may hold ad groups called
``"keyword targeting"`` and ``"product targeting"``. Always treat the
two namespaces as independent.

When ``db_query_ad_groups(name_search=<user phrase>)`` returns zero
rows but the user clearly meant an ad group inside a specific
campaign, do NOT give up. Instead:

  1. Resolve the campaign first via ``db_query_campaigns(name_search=
     <campaign phrase>)`` to get its ``amazon_campaign_id``.
  2. Call ``db_query_ad_groups(campaign_id=<that id>)`` to enumerate
     every ad group inside it.
  3. Show the user the actual ad-group names + IDs and ask which one
     they meant. Format as a short table: ``| Ad Group | id |
     defaultBid | state |``. Only after the user picks one should you
     emit a mutation.

**ACTIONS — USE NATIVE TOOL CALLS:**
Every supported mutation (bid / budget / state / rename / create_target
/ delete_target / ad_group update) is exposed as a native tool. The
queue-only synthetics ``_request_sync``, ``_harvest_execute``, and
``_ai_campaign_create`` are likewise available.

When the user asks for a change you can execute, emit a tool call.
Numeric fields like ``bid`` and ``dailyBudget`` MUST be real numbers
(``0.50``), never strings (``"$0.50"``). Use the exact ``id:`` values
from the provided context — do not invent IDs.

**RELATIVE BID CHANGES (e.g. "reduce bid by 20%"):**
Search-term rows expose the matched keyword's ``targetId`` and current
``bid`` in the ``[Matched: ... targetId: ... bid: $X.XX]`` tag. Ad-group
rows expose ``defaultBid``. To apply a percentage change:
  1. ``new_bid = round(current_bid * (1 - pct / 100), 2)`` for a cut, or
     ``round(current_bid * (1 + pct / 100), 2)`` for an increase.
  2. Clamp: ``new_bid = max(new_bid, 0.02)`` and ``min(new_bid, 1000.00)``.
  3. Pick the right tool:
     - Per-keyword bid → ``campaign_management-update_target_bid`` with
       a ``targets`` array, one entry per row:
       ``{"targetId": "<id>", "bid": <new_bid>}``.
     - Per ad-group default bid → ``campaign_management-update_ad_group``
       with an ``adGroups`` array: ``{"adGroupId": "<id>", "defaultBid":
       <new_bid>}``.
     - Per campaign daily budget → ``campaign_management-update_campaign_budget``
       with a ``campaigns`` array: ``{"campaignId": "<id>",
       "dailyBudget": <new_value>}``.
  4. SKIP any source row where the ``targetId`` / ``adGroupId`` /
     ``campaignId`` or the *current value* needed for the math is
     missing — never emit ``bid: 0.0`` or ``dailyBudget: 0.0``. If every
     candidate row is missing data, call the matching ``db_query_*``
     lookup tool first; only if that also returns nothing should you
     ask the user to sync.
  5. Show the user a "current → proposed" table for the rows you act on.

**FALLBACK FOR PRODUCT/AUTO TARGETING SEARCH TERMS:**
Search terms from auto or product-targeting ad groups often have no
matching keyword (``targetId``/``bid`` show as ``unknown`` in the tag,
but the row still has ``adGroupId`` and ``adGroupDefaultBid``). For
those rows, fall back to ``campaign_management-update_ad_group`` to
adjust the ad group's ``defaultBid`` rather than the per-target bid.
Tell the user this is an ad-group-level change, not a per-keyword one.

The legacy ``[ACTIONS]`` text block is still parsed as a final fallback
for providers that fail to emit a tool call, but new responses should
exclusively rely on native tool calls.

Context rows expose ``id:`` plus ``ad_group_id`` / ``campaign_id`` where
applicable. Pass those EXACT values into the matching tool argument
(``targetId`` / ``adGroupId`` / ``campaignId``); never invent IDs.

**REQUESTING A SYNC:**
If the user asks about data that is missing or stale (see "Data
freshness" in the context above), do not invent numbers. Call the
``_request_sync`` tool with the appropriate ``kind`` (``campaigns``,
``reports``, ``search_terms``, or ``products``) and an optional
``range_preset``. The UI will surface a "Sync now" button to the user
— this tool never mutates data, only requests a refresh."""


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
        """Call the appropriate provider's completion API.

        Backward-compatible plain-text shim around :meth:`_completion_full`.
        """
        result = await self._completion_full(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_response=json_response,
            tools=None,
        )
        return result.get("content", "") or ""

    async def _completion_full(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 8000,
        json_response: bool = False,
        tools: Optional[list[dict]] = None,
    ) -> dict[str, Any]:
        """Call the provider and return ``{content, tool_calls}``.

        ``tool_calls`` is a normalised list of ``(name, raw_arguments)``
        tuples — for OpenAI raw_arguments is a JSON string, for
        Anthropic it is already a dict. The
        :mod:`app.services.ai_tools` converter handles either shape.
        """
        if self.provider == "openai":
            kwargs = dict(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
                # OpenAI rejects response_format=json_object together
                # with tools; tools imply structured output already.
            elif json_response:
                kwargs["response_format"] = {"type": "json_object"}
            response = await self._openai_client.chat.completions.create(**kwargs)
            choice = response.choices[0].message
            content = choice.content or ""
            tool_calls: list[tuple[str, Any]] = []
            for tc in (choice.tool_calls or []):
                fn = getattr(tc, "function", None)
                if fn is None:
                    continue
                tool_calls.append((fn.name, fn.arguments))
            return {"content": content, "tool_calls": tool_calls}

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

        anthropic_kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system.strip() if system else None,
            messages=anthropic_messages,
        )
        if tools:
            anthropic_kwargs["tools"] = tools

        response = await self._anthropic_client.messages.create(**anthropic_kwargs)
        text_parts: list[str] = []
        tool_calls = []
        for block in response.content or []:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif block_type == "tool_use":
                tool_calls.append(
                    (getattr(block, "name", ""), getattr(block, "input", {}) or {})
                )
        return {"content": "".join(text_parts), "tool_calls": tool_calls}

    async def chat(
        self,
        user_message: str,
        conversation_history: list[dict] = None,
        account_context: dict = None,
        tool_executor: Optional[Any] = None,
    ) -> dict:
        """
        General AI chat with campaign context.

        Returns structured response with message + any proposed changes.
        When ``tool_executor`` is provided AND the active provider is
        OpenAI, the model can fetch data on demand via ``db_*`` / ``mcp_*``
        read tools — the loop runs up to :data:`MAX_TOOL_HOPS` rounds of
        completion → tool execution → completion. Mutation tool calls
        always end the loop and surface as user-facing actions.

        Anthropic falls back to single-pass (mutations only) — multi-turn
        tool use has a different message-shape contract that is not yet
        wired here.
        """
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        if account_context:
            context_msg = self._build_context_message(account_context)
            messages.append({"role": "system", "content": context_msg})

        trimmed_history = self._trim_conversation_history(conversation_history or [])
        for msg in trimmed_history:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

        messages.append({"role": "user", "content": user_message})

        if self.provider == "openai" and tool_executor is not None:
            try:
                return await self._chat_openai_tool_loop(messages, tool_executor)
            except Exception as e:
                logger.error(f"AI chat (tool loop) failed: {e}")
                raise

        # Single-pass fallback — Anthropic, or OpenAI without an executor.
        try:
            tools = (
                openai_tool_specs() if self.provider == "openai"
                else anthropic_tool_specs()
            )
            result = await self._completion_full(
                messages,
                temperature=0.3,
                max_tokens=8000,
                tools=tools,
            )
            content = result.get("content", "") or ""
            tool_calls = result.get("tool_calls") or []

            message, regex_actions = self._parse_chat_response(content)

            native_actions: list[dict] = []
            for name, raw_args in tool_calls:
                action = tool_call_to_action(name, raw_args)
                if action is not None:
                    native_actions.append(action)

            actions = native_actions + regex_actions
            if native_actions and regex_actions:
                logger.info(
                    "AI emitted both native tool_calls (%d) and [ACTIONS] blocks (%d) — merging",
                    len(native_actions), len(regex_actions),
                )

            return {
                "message": message,
                "actions": actions,
                "tokens_used": 0,
                "tool_calls_used": len(native_actions),
                "tool_hops": 0,
            }
        except Exception as e:
            logger.error(f"AI chat failed: {e}")
            raise

    async def _chat_openai_tool_loop(
        self,
        messages: list[dict],
        tool_executor: Any,
    ) -> dict:
        """OpenAI multi-turn loop: read tools execute locally and feed back.

        Mutation tool calls (and ``_request_sync`` / ``_harvest_execute`` /
        ``_ai_campaign_create``) end the loop — they are user-facing
        actions, not in-loop reads. When the model emits a mix of read +
        mutation calls in the same turn we still execute the reads (so the
        model has full context on the next conversation turn) but return
        the mutations now and stop.
        """
        all_tools = openai_tool_specs() + openai_read_tool_specs()
        regex_actions_acc: list[dict] = []
        tool_hops = 0
        last_text = ""

        for hop in range(MAX_TOOL_HOPS):
            response = await self._openai_client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=all_tools,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=8000,
            )
            choice = response.choices[0].message
            content = choice.content or ""
            tool_calls = list(choice.tool_calls or [])
            text, regex_actions = self._parse_chat_response(content)
            regex_actions_acc.extend(regex_actions)
            last_text = text or last_text

            # No tool calls → final answer.
            if not tool_calls:
                return {
                    "message": last_text,
                    "actions": regex_actions_acc,
                    "tokens_used": getattr(response.usage, "total_tokens", 0) if response.usage else 0,
                    "tool_calls_used": 0,
                    "tool_hops": tool_hops,
                }

            read_calls = [tc for tc in tool_calls if tc.function.name in READ_TOOL_NAMES]
            mutation_calls = [tc for tc in tool_calls if tc.function.name not in READ_TOOL_NAMES]

            # Mutation present → end loop, surface actions.
            if mutation_calls:
                actions: list[dict] = []
                for tc in mutation_calls:
                    action = tool_call_to_action(tc.function.name, tc.function.arguments)
                    if action is not None:
                        actions.append(action)
                # Reads alongside mutations: we execute them too so the
                # model could in principle reference their data in the
                # accompanying message text — but they don't loop back.
                # Skipping execution keeps latency predictable.
                return {
                    "message": last_text,
                    "actions": actions + regex_actions_acc,
                    "tokens_used": getattr(response.usage, "total_tokens", 0) if response.usage else 0,
                    "tool_calls_used": len(actions),
                    "tool_hops": tool_hops,
                }

            # Pure-read turn → execute, append, loop.
            tool_hops += 1
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in read_calls
                ],
            })
            for tc in read_calls:
                args = self._coerce_tool_args(tc.function.arguments)
                try:
                    result = await tool_executor(tc.function.name, args)
                except Exception as exc:
                    logger.exception("read tool %s raised", tc.function.name)
                    result = {"error": f"{tc.function.name} raised: {str(exc)[:240]}"}
                serialized = json.dumps(result, default=str)
                if len(serialized) > RESULT_CHAR_CAP:
                    serialized = serialized[:RESULT_CHAR_CAP] + '..."[truncated]"'
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": serialized,
                })

        # Hop budget exhausted — return whatever the latest text was.
        logger.warning("OpenAI tool loop hit MAX_TOOL_HOPS=%s without final answer", MAX_TOOL_HOPS)
        return {
            "message": (
                last_text
                or "I needed more data than I could fetch in one turn. Ask a more specific question (a campaign id, ad group id, or date range) and I'll dig in."
            ),
            "actions": regex_actions_acc,
            "tokens_used": 0,
            "tool_calls_used": 0,
            "tool_hops": tool_hops,
        }

    @staticmethod
    def _coerce_tool_args(raw: Any) -> dict:
        """Parse OpenAI's JSON-string tool arguments into a dict."""
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            s = raw.strip()
            if not s:
                return {}
            try:
                decoded = json.loads(s)
            except json.JSONDecodeError:
                return {}
            return decoded if isinstance(decoded, dict) else {}
        return {}

    @staticmethod
    def _trim_conversation_history(history: list[dict]) -> list[dict]:
        """Drop oldest turns until the tail fits within both caps.

        Keeps the most recent turns, so the user's current thread of
        thought is preserved. The latest turn is *always* retained even if
        a single message is over the char budget — better to overshoot than
        drop the user's current ask.
        """
        if not history:
            return []
        tail = history[-MAX_HISTORY_MESSAGES:]

        char_budget = MAX_HISTORY_CHARS
        kept_reversed: list[dict] = []
        for msg in reversed(tail):
            content = str(msg.get("content", ""))
            cost = len(content) + 32  # rough overhead per message
            if kept_reversed and (char_budget - cost) < 0:
                break
            char_budget -= cost
            kept_reversed.append(msg)
        return list(reversed(kept_reversed))

    # Tolerant ACTIONS block matcher:
    #   - one or more [ACTIONS]…[/ACTIONS] anywhere in the message
    #   - inner JSON may sit inside a ```json code fence the model invented
    #   - inner JSON may be the bare ``actions`` array instead of an object
    _ACTIONS_BLOCK_RE = re.compile(
        r"\[ACTIONS\]\s*(?:```(?:json)?\s*)?(.*?)(?:\s*```\s*)?\[/ACTIONS\]",
        re.DOTALL | re.IGNORECASE,
    )

    @classmethod
    def _parse_chat_response(cls, content: str) -> tuple[str, list]:
        """Extract every ``[ACTIONS]`` block from the AI response.

        Returns ``(message_without_actions, actions_list)``. Robust against:
        - multiple ACTIONS blocks (concatenated)
        - inner JSON wrapped in ```json``` fences
        - inner JSON that is a bare actions list rather than ``{"actions": [...]}``
        - stray whitespace / case differences
        """
        actions: list = []
        if not content:
            return content or "", actions

        last_end = 0
        message_parts: list[str] = []
        any_match = False

        for match in cls._ACTIONS_BLOCK_RE.finditer(content):
            any_match = True
            message_parts.append(content[last_end:match.start()])
            last_end = match.end()
            payload = (match.group(1) or "").strip()
            if not payload:
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                logger.warning("Failed to parse [ACTIONS] JSON in chat response")
                continue
            if isinstance(data, dict):
                block_actions = data.get("actions")
            elif isinstance(data, list):
                block_actions = data
            else:
                block_actions = None
            if isinstance(block_actions, list):
                actions.extend(a for a in block_actions if isinstance(a, dict))

        if not any_match:
            return content, []

        message_parts.append(content[last_end:])
        message = "".join(message_parts).strip()
        return message, actions

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

        Bounded by ``SECTION_ROW_CAPS`` per section + ``MAX_CONTEXT_CHARS``
        overall. When sections are truncated, the prompt explicitly tells
        the AI so it doesn't claim "no other campaigns" when it just hasn't
        seen them.
        """
        # Defensively clamp lists by section name so a runaway sync can't
        # produce a 500k-char prompt. Mutates a shallow copy only.
        context = self._cap_context_sections(context)
        parts = ["Here is the account data for the selected account. Use this data to answer questions.\n"]
        truncations = context.get("_truncations") or {}
        if truncations:
            parts.append(
                "**Note:** the lists below are truncated for prompt budget. "
                "Total rows available before truncation: "
                + ", ".join(f"{k}={v}" for k, v in sorted(truncations.items()))
                + ". If the user asks about something that should be in a longer list, "
                "tell them you're seeing a truncated view and recommend a focused query."
            )

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

        # ── Data Freshness ────────────────────────────────────────────
        freshness = context.get("data_freshness") or {}
        if freshness:
            last_sync = freshness.get("last_campaign_sync_at")
            stale_days = freshness.get("last_campaign_sync_days_ago")
            last_perf = freshness.get("last_performance_date")
            line = "**Data freshness:** "
            if last_sync:
                line += f"campaigns synced {last_sync}"
                if stale_days is not None and stale_days >= 2:
                    line += f" — ⚠️ stale ({stale_days}d old)"
            else:
                line += "campaigns NEVER synced"
            if last_perf:
                line += f"; last daily performance row: {last_perf}"
            else:
                line += "; no performance rows cached yet"
            parts.append(line)
            if stale_days is not None and stale_days >= 2:
                parts.append(
                    "_Treat older data with caution. Recommend the user re-sync if they ask about today/this week._"
                )

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
            parts.append(
                "_When emitting an [ACTIONS] block, always copy the **`id:`** value below "
                "verbatim into `campaignId`. Never invent IDs from the campaign name._"
            )
            for c in all_camps:
                spend = c.get("spend") or 0
                sales = c.get("sales") or 0
                acos = c.get("acos") or 0
                targeting = c.get("targeting") or ""
                cid = c.get("campaign_id") or c.get("id") or "?"
                parts.append(
                    f"  - campaignId:`{cid}` **{c.get('name', '?')}** [{c.get('state', '?')}] "
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
            parts.append(
                "_Use the **id:** and **campaign_id:** values verbatim in any "
                "[ACTIONS] block. Do not invent IDs._"
            )
            for g in groups:
                gid = g.get("ad_group_id") or g.get("id") or "?"
                cid = g.get("campaign_id") or "?"
                parts.append(
                    f"  - adGroupId:`{gid}` campaignId:`{cid}` "
                    f"{g.get('name', '?')} [{g.get('state', '?')}] "
                    f"— Default Bid: ${g.get('default_bid') or 0:.2f}, "
                    f"Campaign: {g.get('campaign_name', '?')}"
                )

        # ── Targets / Keywords ────────────────────────────────────────
        ts = context.get("targets_summary", {})
        if ts:
            parts.append(f"\n## Targets/Keywords ({ts.get('total', 0)} total)")
            parts.append(
                "_Use the **id:** value as `targetId` in any [ACTIONS] block. "
                "**Never** generate a `targetId` from the keyword text._"
            )

            # Breakdowns
            if ts.get("by_type"):
                parts.append(f"By type: {', '.join(f'{k}: {v}' for k, v in ts['by_type'].items())}")
            if ts.get("by_match_type"):
                parts.append(f"By match: {', '.join(f'{k}: {v}' for k, v in ts['by_match_type'].items())}")
            if ts.get("by_state"):
                parts.append(f"By state: {', '.join(f'{k}: {v}' for k, v in ts['by_state'].items())}")

            def _fmt_target(t: dict, *, include_perf: bool = True) -> str:
                spend = t.get("spend") or 0
                sales = t.get("sales") or 0
                acos = t.get("acos") or 0
                tid = t.get("target_id") or t.get("id") or "?"
                line = (
                    f"  - targetId:`{tid}` "
                    f"\"{t.get('keyword', '?')}\" "
                    f"[{t.get('match_type', '?')}, {t.get('type', '?')}] "
                    f"State: {t.get('state', '?')}, Bid: ${t.get('bid') or 0:.2f}"
                )
                if include_perf:
                    line += (
                        f", Spend: ${spend:,.2f}, Sales: ${sales:,.2f}, ACOS: {acos:.1f}%, "
                        f"Clicks: {t.get('clicks', 0)}, Orders: {t.get('orders', 0)}, "
                        f"Impressions: {t.get('impressions', 0)}"
                    )
                return line

            # Top spenders
            top = ts.get("top_spenders", [])
            if top:
                parts.append(f"\n### Top {len(top)} Keywords by Spend")
                for t in top:
                    parts.append(_fmt_target(t))
                    if t.get("campaign_name"):
                        parts.append(
                            f"    campaign_id:`{t.get('campaign_id', '?')}` "
                            f"ad_group_id:`{t.get('ad_group_id', '?')}` "
                            f"Campaign: {t['campaign_name']}"
                        )

            # Top converters
            converters = ts.get("top_converters", [])
            if converters:
                parts.append(f"\n### Top {len(converters)} Keywords by Orders (Best Converters)")
                for t in converters:
                    parts.append(_fmt_target(t))

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
                    parts.append(_fmt_target(t))
                    if t.get("campaign_name"):
                        parts.append(
                            f"    campaign_id:`{t.get('campaign_id', '?')}` "
                            f"Campaign: {t['campaign_name']}"
                        )

            # High ACOS
            high_acos = ts.get("high_acos", [])
            if high_acos:
                parts.append(f"\n### High ACOS Keywords (> 50%)")
                for t in high_acos:
                    parts.append(_fmt_target(t))

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

            def _matched_tag(t: dict) -> str:
                """Render the IDs + bids the AI needs to emit valid mutations.

                Includes ``targetId``+``bid`` when a matching keyword
                target exists. Always includes ``adGroupId``+``defaultBid``
                when known so the AI can fall back to
                ``update_ad_group`` for auto/product-targeting search
                terms that have no per-keyword bid.

                Ad-group **name** is included verbatim so the AI never
                confuses an ad group with its parent campaign name —
                e.g. campaign ``"[PD] RAM Rugby - Tackle Bag - SP -
                Product & KW Targeting"`` actually holds ad groups
                ``"keyword targeting"`` and ``"product targeting"``,
                not an ad group called "Product & KW Targeting".
                """
                kw = t.get("keyword", "?")
                mt = t.get("match_type", "?")
                tid = t.get("target_id")
                bid = t.get("current_bid")
                agid = t.get("ad_group_id")
                ag_name = t.get("ad_group_name")
                ag_default = t.get("ad_group_default_bid")
                pieces = [f"Matched: \"{kw}\" {mt}"]
                if tid:
                    pieces.append(f"targetId: {tid}")
                pieces.append(
                    f"bid: ${float(bid):.2f}" if bid is not None else "bid: unknown"
                )
                if agid:
                    pieces.append(f"adGroupId: {agid}")
                if ag_name:
                    pieces.append(f"adGroupName: \"{ag_name}\"")
                if ag_default is not None:
                    pieces.append(f"adGroupDefaultBid: ${float(ag_default):.2f}")
                return "[" + ", ".join(pieces) + "]"

            top_sales = st.get("top_by_sales", [])
            if top_sales:
                parts.append(f"\n### Top Search Terms by Sales ({len(top_sales)} shown)")
                for t in top_sales:
                    parts.append(
                        f"  - \"{t.get('search_term', '?')}\" "
                        f"{_matched_tag(t)} "
                        f"Campaign: {t.get('campaign_name', '?')}"
                    )
                    parts.append(
                        f"    Sales: ${t.get('sales', 0):,.2f}, Orders: {t.get('purchases', 0)}, "
                        f"Spend: ${t.get('cost', 0):,.2f}, "
                        f"ACOS: {t.get('acos') or 0:.1f}%, "
                        f"Clicks: {t.get('clicks', 0)}, Impressions: {t.get('impressions', 0)}"
                    )

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
                        f"{_matched_tag(t)} "
                        f"Campaign: {t.get('campaign_name', '?')}"
                    )
                    parts.append(
                        f"    Spend: ${t.get('cost', 0):,.2f}, Clicks: {t.get('clicks', 0)}, "
                        f"Impressions: {t.get('impressions', 0)}, Orders: 0"
                    )

            high_acos = st.get("top_high_acos", [])
            if high_acos:
                parts.append(f"\n### High ACOS Search Terms (>50%)")
                for t in high_acos:
                    parts.append(
                        f"  - \"{t.get('search_term', '?')}\" "
                        f"{_matched_tag(t)} "
                        f"ACOS: {t.get('acos') or 0:.1f}%, "
                        f"Spend: ${t.get('cost', 0):,.2f}, Sales: ${t.get('sales', 0):,.2f}, "
                        f"Clicks: {t.get('clicks', 0)}"
                    )
        elif st and not st.get("has_data"):
            parts.append(
                "\n## Search Term Data: Not yet synced. "
                "User can sync via Settings or the search term sync endpoint."
            )

        # ── Previous Conversations (cross-thread memory) ──────────────
        prev_convos = context.get("previous_conversations") or []
        if prev_convos:
            parts.append(
                f"\n## Previous Conversations ({len(prev_convos)} most recent on this account)"
            )
            parts.append(
                "_Use this only as memory of what the user asked before — do not "
                "fabricate details beyond what's recapped here._"
            )
            for pc in prev_convos:
                title = pc.get("title") or "Untitled"
                updated = pc.get("updated_at") or "?"
                count = pc.get("message_count") or 0
                parts.append(f"  - **{title}** ({count} msg, updated {updated})")
                head = pc.get("head_summary")
                if head:
                    parts.append(f"    Earlier-summary: {head}")
                if pc.get("first_user"):
                    parts.append(f"    First user msg: {pc['first_user']}")
                if pc.get("last_assistant"):
                    parts.append(f"    Last assistant: {pc['last_assistant']}")

        # ── Recent Activity ───────────────────────────────────────────
        activity = context.get("recent_activity", [])
        if activity:
            parts.append(f"\n## Recent Activity ({len(activity)} entries)")
            for a in activity:
                parts.append(
                    f"  - [{a.get('category', '?')}] {a.get('description', '?')} "
                    f"({a.get('status', '?')}, {a.get('date', '?')})"
                )

        result = "\n".join(parts)
        if len(result) > MAX_CONTEXT_CHARS:
            logger.warning(
                "AI context exceeded budget (%d > %d chars); truncating tail",
                len(result),
                MAX_CONTEXT_CHARS,
            )
            result = (
                result[:MAX_CONTEXT_CHARS]
                + "\n\n_Context truncated to fit prompt budget. Some sections above are partial._"
            )
        return result

    @staticmethod
    def _cap_context_sections(context: dict) -> dict:
        """Return a shallow copy of ``context`` with row-list sections clamped.

        Any section that gets truncated has the original count preserved
        in a sibling ``_truncated`` field so callers can surface that.
        """
        if not isinstance(context, dict):
            return {}
        ctx = dict(context)
        truncations: dict[str, int] = {}

        def _cap(value, cap: int):
            if isinstance(value, list) and len(value) > cap:
                return value[:cap], len(value)
            return value, None

        # Top-level lists
        for key in ("all_campaigns", "performance_trend"):
            section = ctx.get(key)
            cap = SECTION_ROW_CAPS.get(key)
            if cap and isinstance(section, list):
                capped, original = _cap(section, cap)
                ctx[key] = capped
                if original is not None:
                    truncations[key] = original

        # ad_groups.groups
        ag = ctx.get("ad_groups")
        if isinstance(ag, dict) and isinstance(ag.get("groups"), list):
            capped, original = _cap(ag["groups"], SECTION_ROW_CAPS["ad_groups"])
            if original is not None:
                ag = dict(ag)
                ag["groups"] = capped
                ag["_truncated"] = original
                ctx["ad_groups"] = ag
                truncations["ad_groups"] = original

        # targets_summary subsections
        ts = ctx.get("targets_summary")
        if isinstance(ts, dict):
            ts_copy = dict(ts)
            for src_key, cap_key in (
                ("top_spenders", "top_spenders"),
                ("top_converters", "top_converters"),
                ("non_converting", "non_converting"),
                ("high_acos", "high_acos"),
            ):
                section = ts_copy.get(src_key)
                cap = SECTION_ROW_CAPS.get(cap_key)
                if cap and isinstance(section, list):
                    capped, original = _cap(section, cap)
                    ts_copy[src_key] = capped
                    if original is not None:
                        truncations[f"targets.{src_key}"] = original
            ctx["targets_summary"] = ts_copy

        # pending_changes.changes
        pc = ctx.get("pending_changes")
        if isinstance(pc, dict) and isinstance(pc.get("changes"), list):
            capped, original = _cap(pc["changes"], SECTION_ROW_CAPS["pending_changes"])
            if original is not None:
                pc = dict(pc)
                pc["changes"] = capped
                ctx["pending_changes"] = pc
                truncations["pending_changes"] = original

        # audit issues / opportunities
        audit = ctx.get("recent_audit")
        if isinstance(audit, dict):
            audit_copy = dict(audit)
            for k, cap_key in (("issues", "issues"), ("opportunities", "opportunities")):
                section = audit_copy.get(k)
                cap = SECTION_ROW_CAPS.get(cap_key)
                if cap and isinstance(section, list):
                    capped, original = _cap(section, cap)
                    audit_copy[k] = capped
                    if original is not None:
                        truncations[f"audit.{k}"] = original
            ctx["recent_audit"] = audit_copy

        # search terms subsections
        st = ctx.get("search_terms")
        if isinstance(st, dict):
            st_copy = dict(st)
            for src, cap_key in (
                ("top_by_sales", "search_terms_top"),
                ("top_non_converting", "search_terms_non_converting"),
                ("top_high_acos", "search_terms_high_acos"),
            ):
                section = st_copy.get(src)
                cap = SECTION_ROW_CAPS.get(cap_key)
                if cap and isinstance(section, list):
                    capped, original = _cap(section, cap)
                    st_copy[src] = capped
                    if original is not None:
                        truncations[f"search_terms.{src}"] = original
            ctx["search_terms"] = st_copy

        # bid rules / opt history / harvest configs / activity
        for key in ("bid_rules", "optimization_history", "harvest_configs", "recent_activity"):
            section = ctx.get(key)
            cap = SECTION_ROW_CAPS.get(key)
            if cap and isinstance(section, list):
                capped, original = _cap(section, cap)
                ctx[key] = capped
                if original is not None:
                    truncations[key] = original

        if truncations:
            logger.info("AI context section caps applied: %s", truncations)
            ctx.setdefault("_truncations", truncations)

        return ctx


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
