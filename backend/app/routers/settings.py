"""
Settings Router — Application-wide settings (LLM configuration, etc.)
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models import AppSettings
from app.config import get_settings
from app.crypto import encrypt_value, decrypt_value

router = APIRouter()
settings = get_settings()


def _mask_key(key: str | None) -> str:
    """Return masked representation of API key."""
    if not key:
        return ""
    if len(key) <= 12:
        return "••••••••"
    return key[:6] + "•••••••••••••" + key[-4:]


# ── Available LLMs (GPT up to 5.2, Claude Sonnet) ───────────────────────
# Per https://developers.openai.com/api/docs/guides/latest-model/
AVAILABLE_LLMS = [
    # OpenAI GPT (up to 5.2)
    {"provider": "openai", "model": "gpt-5.2", "label": "GPT-5.2", "description": "Best for complex reasoning and agentic tasks"},
    {"provider": "openai", "model": "gpt-5.2-pro", "label": "GPT-5.2 Pro", "description": "Tough problems requiring harder thinking"},
    {"provider": "openai", "model": "gpt-5.2-codex", "label": "GPT-5.2 Codex", "description": "Coding-optimized for agentic workflows"},
    {"provider": "openai", "model": "gpt-5-mini", "label": "GPT-5 Mini", "description": "Cost-optimized reasoning and chat"},
    {"provider": "openai", "model": "gpt-5-nano", "label": "GPT-5 Nano", "description": "High-throughput, simple tasks"},
    {"provider": "openai", "model": "gpt-4o", "label": "GPT-4o", "description": "Fast multiturn, vision"},
    {"provider": "openai", "model": "gpt-4o-mini", "label": "GPT-4o Mini", "description": "Smaller, faster GPT-4o"},
    # Anthropic Claude
    {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "label": "Claude Sonnet 4", "description": "Balanced performance and capability"},
    {"provider": "anthropic", "model": "claude-3-5-sonnet-20241022", "label": "Claude 3.5 Sonnet", "description": "Previous Sonnet generation"},
]


def _model_id(provider: str, model: str) -> str:
    return f"{provider}:{model}"


def _get_providers_from_row(row: AppSettings | None) -> dict:
    """Check env first, then stored (decrypted) keys."""
    openai_key = settings.openai_api_key
    anthropic_key = settings.anthropic_api_key
    if not openai_key and row and row.openai_api_key:
        openai_key = decrypt_value(row.openai_api_key)
    if not anthropic_key and row and row.anthropic_api_key:
        anthropic_key = decrypt_value(row.anthropic_api_key)
    return {
        "openai": bool(openai_key),
        "anthropic": bool(anthropic_key),
    }


def _get_paapi_from_row(row: AppSettings | None) -> tuple[str | None, str | None, str | None]:
    """Get PA-API credentials: (access_key, secret_key, partner_tag)."""
    access = settings.paapi_access_key or (decrypt_value(getattr(row, "paapi_access_key", None)) if row and getattr(row, "paapi_access_key", None) else None)
    secret = settings.paapi_secret_key or (decrypt_value(getattr(row, "paapi_secret_key", None)) if row and getattr(row, "paapi_secret_key", None) else None)
    tag = settings.paapi_partner_tag or (getattr(row, "paapi_partner_tag", None) if row else None)
    return (access, secret, tag)


async def get_effective_api_keys(db: AsyncSession) -> tuple[str | None, str | None]:
    """
    Get effective API keys: env vars take precedence over stored.
    Returns (openai_key, anthropic_key). Used by AI router.
    """
    result = await db.execute(select(AppSettings).limit(1))
    row = result.scalar_one_or_none()
    openai_key = settings.openai_api_key or (decrypt_value(row.openai_api_key) if row and row.openai_api_key else None)
    anthropic_key = settings.anthropic_api_key or (decrypt_value(row.anthropic_api_key) if row and row.anthropic_api_key else None)
    return (openai_key, anthropic_key)


# ── Request/Response Models ───────────────────────────────────────────

class LLMSettingsResponse(BaseModel):
    default_llm_id: Optional[str]
    enabled_llms: list[dict]
    available_llms: list[dict]
    providers_configured: dict


class LLMSettingsUpdate(BaseModel):
    default_llm_id: Optional[str] = None
    enabled_llms: Optional[list[dict]] = None


class APIKeysUpdate(BaseModel):
    openai_api_key: Optional[str] = None  # Set to "" to clear
    anthropic_api_key: Optional[str] = None  # Set to "" to clear
    paapi_access_key: Optional[str] = None
    paapi_secret_key: Optional[str] = None
    paapi_partner_tag: Optional[str] = None


class APIKeysResponse(BaseModel):
    openai_configured: bool
    anthropic_configured: bool
    openai_source: str  # "env" | "settings"
    anthropic_source: str
    paapi_configured: bool


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/api-keys", response_model=APIKeysResponse)
async def get_api_keys(db: AsyncSession = Depends(get_db)):
    """Get API key configuration status (masked)."""
    result = await db.execute(select(AppSettings).limit(1))
    row = result.scalar_one_or_none()
    providers = _get_providers_from_row(row)
    paapi_access, paapi_secret, paapi_tag = _get_paapi_from_row(row)
    openai_source = "env" if settings.openai_api_key else ("settings" if row and row.openai_api_key else "none")
    anthropic_source = "env" if settings.anthropic_api_key else ("settings" if row and row.anthropic_api_key else "none")
    return APIKeysResponse(
        openai_configured=providers["openai"],
        anthropic_configured=providers["anthropic"],
        openai_source=openai_source,
        anthropic_source=anthropic_source,
        paapi_configured=bool(paapi_access and paapi_secret and paapi_tag),
    )


@router.put("/api-keys")
async def update_api_keys(payload: APIKeysUpdate, db: AsyncSession = Depends(get_db)):
    """Save API keys (encrypted). Env vars take precedence when set."""
    result = await db.execute(select(AppSettings).limit(1))
    row = result.scalar_one_or_none()
    if not row:
        row = AppSettings(default_llm_id=None, enabled_llms=[])
        db.add(row)
        await db.flush()

    if payload.openai_api_key is not None:
        row.openai_api_key = encrypt_value(payload.openai_api_key.strip() or None) if payload.openai_api_key else None
    if payload.anthropic_api_key is not None:
        row.anthropic_api_key = encrypt_value(payload.anthropic_api_key.strip() or None) if payload.anthropic_api_key else None
    if payload.paapi_access_key is not None:
        row.paapi_access_key = encrypt_value(payload.paapi_access_key.strip() or None) if payload.paapi_access_key else None
    if payload.paapi_secret_key is not None:
        row.paapi_secret_key = encrypt_value(payload.paapi_secret_key.strip() or None) if payload.paapi_secret_key else None
    if payload.paapi_partner_tag is not None:
        row.paapi_partner_tag = payload.paapi_partner_tag.strip() or None

    paapi_ok = bool(row.paapi_access_key and row.paapi_secret_key and row.paapi_partner_tag)
    return {"openai_configured": bool(row.openai_api_key), "anthropic_configured": bool(row.anthropic_api_key), "paapi_configured": paapi_ok}


@router.get("/llm", response_model=LLMSettingsResponse)
async def get_llm_settings(db: AsyncSession = Depends(get_db)):
    """
    Get current LLM settings: default model, enabled models, and available options.
    Available models are filtered by which API keys are configured (env or Settings).
    """
    result = await db.execute(select(AppSettings).limit(1))
    row = result.scalar_one_or_none()
    providers = _get_providers_from_row(row)

    # Filter available LLMs to only those with API keys
    available = [
        llm for llm in AVAILABLE_LLMS
        if providers.get(llm["provider"], False)
    ]
    if not available and (providers.get("openai") or providers.get("anthropic")):
        available = AVAILABLE_LLMS

    default_id = row.default_llm_id if row else None
    enabled = row.enabled_llms if row and row.enabled_llms else []

    # If no enabled list yet, default to all available
    if not enabled and available:
        enabled = [
            {"provider": llm["provider"], "model": llm["model"], "label": llm["label"]}
            for llm in available
        ]
    # If no default, use first available
    if not default_id and enabled:
        default_id = _model_id(enabled[0]["provider"], enabled[0]["model"])

    return LLMSettingsResponse(
        default_llm_id=default_id,
        enabled_llms=enabled,
        available_llms=available,
        providers_configured=providers,
    )


@router.put("/llm")
async def update_llm_settings(payload: LLMSettingsUpdate, db: AsyncSession = Depends(get_db)):
    """Update default LLM and/or enabled LLMs list."""
    result = await db.execute(select(AppSettings).limit(1))
    row = result.scalar_one_or_none()
    if not row:
        row = AppSettings(default_llm_id=None, enabled_llms=[])
        db.add(row)
        await db.flush()

    if payload.default_llm_id is not None:
        row.default_llm_id = payload.default_llm_id or None
    if payload.enabled_llms is not None:
        # Validate: each must have provider and model
        validated = []
        for item in payload.enabled_llms:
            if isinstance(item, dict) and item.get("provider") and item.get("model"):
                validated.append({
                    "provider": item["provider"],
                    "model": item["model"],
                    "label": item.get("label", item["model"]),
                })
        row.enabled_llms = validated

    return {"default_llm_id": row.default_llm_id, "enabled_llms": row.enabled_llms}
