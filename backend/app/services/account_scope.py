"""
Helpers for deciding whether campaign metadata queries can safely run for
the active Amazon Ads account selection.
"""

from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Credential

CAMPAIGN_SYNC_SCOPE_REQUIRED_DETAIL = (
    "Campaign sync requires a single marketplace child account/profile. "
    "Discover accounts and select a marketplace profile before syncing campaigns."
)
CAMPAIGN_SYNC_GLOBAL_ACCOUNT_DETAIL = (
    "Campaign sync is not supported for global advertiser accounts yet. "
    "Amazon's campaign-management queries require a primary marketplace resource id "
    "that is not available in the current account scope. Use reporting/search-term/product syncs "
    "for accurate performance data, or switch to a non-global standalone marketplace account."
)
CAMPAIGN_SYNC_UNKNOWN_PROFILE_DETAIL = (
    "The active profile could not be matched to a discovered marketplace account. "
    "Rediscover accounts and re-select the marketplace profile before syncing campaigns."
)
CAMPAIGN_SYNC_NON_MARKETPLACE_DETAIL = (
    "Campaign sync requires a marketplace-scoped advertising profile. "
    "Select a specific marketplace account/profile before syncing campaigns."
)


def is_marketplace_child_account(account: Optional[Account]) -> bool:
    """True when the account resolves to a concrete marketplace/profile."""
    if not account:
        return False

    raw = account.raw_data or {}
    marketplace_alt = raw.get("marketplace_alt")
    if isinstance(marketplace_alt, dict) and (
        marketplace_alt.get("countryCode") or marketplace_alt.get("profileId")
    ):
        return True

    return bool(account.profile_id and account.marketplace)


def is_global_root_account(account: Optional[Account]) -> bool:
    """True for a global advertiser/root account, not a marketplace child row."""
    if not account:
        return False

    raw = account.raw_data or {}
    looks_global = raw.get("isGlobalAccount") or (account.account_type or "").lower() == "global"
    return bool(looks_global and not is_marketplace_child_account(account))


def is_global_advertiser_account(account: Optional[Account]) -> bool:
    """True when the discovered row belongs to a global advertiser account."""
    if not account:
        return False
    raw = account.raw_data or {}
    return bool(raw.get("isGlobalAccount") or (account.account_type or "").lower() == "global")


def get_campaign_sync_scope_error(
    account: Optional[Account],
    profile_id: Optional[str],
) -> Optional[str]:
    """Return the user-facing reason campaign metadata sync is unsafe.

    Order matters:
    1. A marketplace child profile is always allowed, even when the parent
       account is flagged ``isGlobalAccount`` — Amazon discovers the child via
       ``marketplace_alt`` and exposes a marketplace-scoped profileId we can
       use directly.
    2. Only true *root* global accounts (no marketplace_alt resolution) are
       blocked.
    """
    if not profile_id:
        return CAMPAIGN_SYNC_SCOPE_REQUIRED_DETAIL
    if account is None:
        return CAMPAIGN_SYNC_UNKNOWN_PROFILE_DETAIL
    if is_marketplace_child_account(account):
        return None
    if is_global_root_account(account):
        return CAMPAIGN_SYNC_GLOBAL_ACCOUNT_DETAIL
    return CAMPAIGN_SYNC_NON_MARKETPLACE_DETAIL


async def resolve_campaign_sync_scope(
    db: AsyncSession,
    cred: Credential,
    profile_id_override: Optional[str] = None,
) -> Tuple[Optional[Account], Optional[str]]:
    """
    Resolve the active discovered account for campaign metadata queries and
    validate that it is a concrete marketplace child profile.
    """
    profile_id = profile_id_override if profile_id_override is not None else cred.profile_id
    if not profile_id:
        return None, CAMPAIGN_SYNC_SCOPE_REQUIRED_DETAIL

    result = await db.execute(
        select(Account).where(
            Account.credential_id == cred.id,
            Account.profile_id == profile_id,
        )
    )
    account = result.scalar_one_or_none()
    return account, get_campaign_sync_scope_error(account, profile_id)
