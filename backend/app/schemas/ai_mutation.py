"""Pydantic schemas for AI-proposed mutations.

These wrap the legacy ``ai_action_validator.ValidationResult`` dataclass
in proper :mod:`pydantic` models so:

* the Anthropic / OpenAI structured-output fallback can validate
  responses with the same shape used by the rest of the pipeline;
* the API layer can serialise validator output without the
  ``dataclasses.asdict()`` boilerplate;
* future consumers (frontend types via OpenAPI) get a typed contract
  for ``actions[].validator_warnings`` and similar fields.

The intent is *parity*, not duplication — these are thin wrappers and
the existing dataclass-based code path keeps working.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class MutationProposal(BaseModel):
    """A single AI-proposed mutation, validated and ready for the queue."""

    tool: str = Field(..., description="MCP tool name or synthetic queue tool")
    arguments: dict[str, Any] = Field(default_factory=dict)
    scope: Optional[str] = Field(
        default=None,
        description="'inline' (run on apply) | 'queue' (multi-step / synthetic)",
    )
    label: Optional[str] = None
    reason: Optional[str] = None
    change_type: Optional[str] = None
    entity_name: Optional[str] = None
    entity_id: Optional[str] = None
    current_value: Optional[str] = None
    proposed_value: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    estimated_impact: Optional[str] = None
    priority: Optional[str] = None
    validator_warnings: list[str] = Field(default_factory=list)


class MutationValidationResult(BaseModel):
    """Outcome of validating one proposal — Pydantic mirror of ValidationResult."""

    ok: bool
    error: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    tool: Optional[str] = None
    arguments: Optional[dict[str, Any]] = None
    proposal: Optional[MutationProposal] = None


class MutationBatchValidationResult(BaseModel):
    """Outcome of validating a batch of proposals."""

    accepted: list[MutationProposal] = Field(default_factory=list)
    rejected: list[MutationValidationResult] = Field(default_factory=list)


def proposal_from_action(action: dict[str, Any]) -> MutationProposal:
    """Build a :class:`MutationProposal` from a raw AI action dict."""
    return MutationProposal(
        tool=str(action.get("tool") or ""),
        arguments=action.get("arguments") or {},
        scope=action.get("scope"),
        label=action.get("label"),
        reason=action.get("reason"),
        change_type=action.get("change_type"),
        entity_name=action.get("entity_name"),
        entity_id=action.get("entity_id"),
        current_value=action.get("current_value"),
        proposed_value=action.get("proposed_value"),
        confidence=action.get("confidence"),
        estimated_impact=action.get("estimated_impact"),
        priority=action.get("priority"),
        validator_warnings=list(action.get("validator_warnings") or []),
    )


__all__ = [
    "MutationProposal",
    "MutationValidationResult",
    "MutationBatchValidationResult",
    "proposal_from_action",
]
