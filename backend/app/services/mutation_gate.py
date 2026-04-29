"""Single mutation gate for all MCP tool dispatch.

Replaces the implicit ``[ACTIONS]`` regex parsing pattern with an
explicit allow-list + read/mutate classifier. Every tool the assistant
can ask the system to run goes through one of these helpers, so:

* the AI assistant cannot accidentally invoke a tool that bypasses the
  approval queue;
* read tools execute immediately and return data;
* mutating tools either run inline (user already approved) or get
  packaged into a ``requires_human_approval`` envelope the UI can route
  into the queue;
* every mutating tool call gets size-checked + sanitised.

Inspired by the adsynth ``mutation_gate.py`` pattern. Amazon-specific
tool names are wired below — extend the frozensets when new tools are
introduced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.ai_tools import TOOL_NAMES as AI_TOOL_NAMES
from app.utils import normalize_mcp_call

logger = logging.getLogger(__name__)


# ── Tool classification ─────────────────────────────────────────────

# Mutating MCP tools we let the AI propose. Any name not in here is
# treated as read-only and runs immediately.
MUTATING_CAMPAIGN_TOOLS: frozenset[str] = frozenset({
    "campaign_management-create_campaign",
    "campaign_management-create_ad_group",
    "campaign_management-create_ad",
    "campaign_management-create_target",
    "campaign_management-update_campaign",
    "campaign_management-update_campaign_state",
    "campaign_management-update_campaign_budget",
    "campaign_management-update_ad_group",
    "campaign_management-update_ad",
    "campaign_management-update_target",
    "campaign_management-update_target_bid",
    "campaign_management-delete_campaign",
    "campaign_management-delete_ad_group",
    "campaign_management-delete_ad",
    "campaign_management-delete_target",
    "campaign_management-delete_ad_association",
    "campaign_management-create_campaign_harvest_targets",
})

# Synthetic, non-MCP "queue-only" tools the assistant uses to escalate
# to a multi-step service or an advisory UI hint.
SYNTHETIC_QUEUE_TOOLS: frozenset[str] = frozenset({
    "_harvest_execute",
    "_ai_campaign_create",
    "_request_sync",
})

ALL_MUTATING_TOOLS: frozenset[str] = MUTATING_CAMPAIGN_TOOLS | SYNTHETIC_QUEUE_TOOLS


def is_mutation(tool: str) -> bool:
    """True when ``tool`` writes to Amazon Ads or queues a write."""
    if not isinstance(tool, str):
        return False
    if tool in ALL_MUTATING_TOOLS:
        return True
    # Defensive — any future create_* / update_* / delete_* / set_* MCP
    # tool we forgot to enumerate gets treated as a mutation.
    if tool.startswith("campaign_management-") and any(
        tool.split("-", 1)[1].startswith(prefix)
        for prefix in ("create_", "update_", "delete_", "set_", "patch_")
    ):
        return True
    return False


# ── Sanitiser for queued mutation arguments ─────────────────────────

MAX_ARGUMENT_BYTES = 32_000  # 32 KB is plenty for a queued mutation
MAX_TARGETS_PER_CALL = 200
MAX_CAMPAIGNS_PER_CALL = 200
MAX_AD_GROUPS_PER_CALL = 200
MAX_TARGET_IDS_PER_DELETE = 500


def _sanitiser_rejected(original: Optional[dict], clean: dict) -> bool:
    """True when the sanitiser emptied a non-empty payload (overflow)."""
    if not isinstance(original, dict) or not original:
        return False
    return clean == {}


def sanitize_mutation_queue_args(
    tool: str,
    arguments: Optional[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """Trim oversized lists / strip unknown bytes from queued args.

    Returns ``(clean_args, warnings)``. The validator runs after this so
    semantic checks (target exists, budget in range, etc.) still apply
    — we just stop a runaway plan from blowing the JSON column or the
    LLM token budget by capping container sizes.
    """
    import json

    warnings: list[str] = []
    args = arguments if isinstance(arguments, dict) else {}
    args = json.loads(json.dumps(args, default=str))  # cheap deep-copy

    body = args.get("body") if isinstance(args.get("body"), dict) else None

    def _clip_list(parent: dict, key: str, cap: int, label: str) -> None:
        items = parent.get(key)
        if isinstance(items, list) and len(items) > cap:
            warnings.append(
                f"{label} truncated from {len(items)} to {cap}"
            )
            parent[key] = items[:cap]

    if isinstance(body, dict):
        _clip_list(body, "targets", MAX_TARGETS_PER_CALL, f"{tool}.body.targets")
        _clip_list(body, "campaigns", MAX_CAMPAIGNS_PER_CALL, f"{tool}.body.campaigns")
        _clip_list(body, "adGroups", MAX_AD_GROUPS_PER_CALL, f"{tool}.body.adGroups")
        _clip_list(body, "ads", MAX_AD_GROUPS_PER_CALL, f"{tool}.body.ads")
        _clip_list(body, "targetIds", MAX_TARGET_IDS_PER_DELETE, f"{tool}.body.targetIds")

    encoded = json.dumps(args, default=str)
    if len(encoded) > MAX_ARGUMENT_BYTES:
        warnings.append(
            f"{tool}: arguments exceed {MAX_ARGUMENT_BYTES} bytes "
            f"({len(encoded)} bytes); rejecting"
        )
        return {}, warnings

    return args, warnings


# ── Run-tool dispatcher ─────────────────────────────────────────────

@dataclass
class GateResult:
    """Outcome of a single :func:`run_tool` invocation."""

    ok: bool
    tool: str
    arguments: dict
    result: Any = None
    error: Optional[str] = None
    requires_human_approval: bool = False
    approval_reason: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "arguments": self.arguments,
            "result": self.result,
            "error": self.error,
            "requires_human_approval": self.requires_human_approval,
            "approval_reason": self.approval_reason,
            "warnings": self.warnings,
        }


async def run_tool(
    client,
    tool: str,
    arguments: Optional[dict] = None,
    *,
    allow_mutations: bool = False,
) -> GateResult:
    """Single entry point for AI / cron / API tool dispatch.

    Args:
        client: An :class:`AmazonAdsMCP` (or test double exposing
            ``call_tool``).
        tool: Tool name. Read tools execute directly; mutating tools
            either execute when ``allow_mutations`` is True or return
            ``requires_human_approval=True`` so the caller can persist
            into the approval queue.
        arguments: Tool body / args.
        allow_mutations: When False (default) mutating tools never run
            — the gate forces them through the approval queue first.
    """
    raw_tool = (tool or "").strip()
    raw_args = arguments if isinstance(arguments, dict) else {}

    if not raw_tool:
        return GateResult(ok=False, tool=raw_tool, arguments=raw_args, error="tool name is required")

    if raw_tool.startswith("_"):
        if raw_tool not in SYNTHETIC_QUEUE_TOOLS:
            return GateResult(
                ok=False, tool=raw_tool, arguments=raw_args,
                error=f"Unknown synthetic tool {raw_tool!r}",
            )
        clean_args, warnings = sanitize_mutation_queue_args(raw_tool, raw_args)
        if _sanitiser_rejected(raw_args, clean_args):
            return GateResult(
                ok=False, tool=raw_tool, arguments=raw_args,
                error="arguments rejected by sanitiser (oversized payload)",
                warnings=warnings,
            )
        if not allow_mutations:
            return GateResult(
                ok=True, tool=raw_tool, arguments=clean_args,
                requires_human_approval=True,
                approval_reason=f"{raw_tool} is queue-only and must be approved by a human",
                warnings=warnings,
            )
        # Synthetic tools have no MCP equivalent — caller (approvals
        # router) handles execution via the matching service.
        return GateResult(
            ok=False, tool=raw_tool, arguments=clean_args,
            error="run_tool cannot directly execute synthetic queue tools; "
            "route _harvest_execute / _ai_campaign_create through their service",
            warnings=warnings,
        )

    normalized_tool, normalized_args = normalize_mcp_call(raw_tool, raw_args)

    if is_mutation(normalized_tool):
        clean_args, warnings = sanitize_mutation_queue_args(normalized_tool, normalized_args)
        if _sanitiser_rejected(normalized_args, clean_args):
            return GateResult(
                ok=False, tool=normalized_tool, arguments=normalized_args,
                error="arguments rejected by sanitiser (oversized payload)",
                warnings=warnings,
            )
        if not allow_mutations:
            return GateResult(
                ok=True, tool=normalized_tool, arguments=clean_args,
                requires_human_approval=True,
                approval_reason=f"{normalized_tool} mutates account state",
                warnings=warnings,
            )
        try:
            result = await client.call_tool(normalized_tool, clean_args)
        except Exception as exc:
            return GateResult(
                ok=False, tool=normalized_tool, arguments=clean_args,
                error=str(exc), warnings=warnings,
            )
        return GateResult(
            ok=True, tool=normalized_tool, arguments=clean_args,
            result=result, warnings=warnings,
        )

    # Read tool — runs immediately.
    try:
        result = await client.call_tool(normalized_tool, normalized_args)
    except Exception as exc:
        return GateResult(
            ok=False, tool=normalized_tool, arguments=normalized_args, error=str(exc),
        )
    return GateResult(
        ok=True, tool=normalized_tool, arguments=normalized_args, result=result,
    )


def assert_known_ai_tool(tool: str) -> None:
    """Raise when an AI-emitted tool isn't on the assistant allow-list.

    Defence-in-depth — the OpenAI / Anthropic SDK already rejects
    function names not in the schema, but a manually-typed AIService
    test or a future provider without strict tool name enforcement
    could still send a string. The validator + this assertion ensure
    we never dispatch off-list.
    """
    if tool not in AI_TOOL_NAMES:
        raise ValueError(f"Tool {tool!r} is not on the AI assistant allow-list")
