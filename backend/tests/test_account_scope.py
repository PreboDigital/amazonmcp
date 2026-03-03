from types import SimpleNamespace

from app.services.account_scope import (
    CAMPAIGN_SYNC_GLOBAL_ACCOUNT_DETAIL,
    CAMPAIGN_SYNC_SCOPE_REQUIRED_DETAIL,
    get_campaign_sync_scope_error,
    is_global_root_account,
    is_marketplace_child_account,
)


def _account(**overrides):
    base = {
        "profile_id": None,
        "marketplace": None,
        "account_type": None,
        "raw_data": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_marketplace_child_account_detected_from_profile_and_marketplace():
    account = _account(profile_id="123", marketplace="US")

    assert is_marketplace_child_account(account) is True
    assert is_global_root_account(account) is False
    assert get_campaign_sync_scope_error(account, "123") is None


def test_marketplace_child_account_detected_from_marketplace_alt():
    account = _account(
        profile_id="123",
        account_type="global",
        raw_data={
            "isGlobalAccount": True,
            "marketplace_alt": {"countryCode": "GB", "profileId": "123"},
        },
    )

    assert is_marketplace_child_account(account) is True
    assert is_global_root_account(account) is False
    assert get_campaign_sync_scope_error(account, "123") is None


def test_global_root_account_is_blocked_for_campaign_sync():
    account = _account(
        account_type="global",
        raw_data={"isGlobalAccount": True},
    )

    assert is_marketplace_child_account(account) is False
    assert is_global_root_account(account) is True
    assert get_campaign_sync_scope_error(account, "global-root") == CAMPAIGN_SYNC_GLOBAL_ACCOUNT_DETAIL


def test_missing_profile_is_blocked_before_any_account_lookup():
    assert get_campaign_sync_scope_error(None, None) == CAMPAIGN_SYNC_SCOPE_REQUIRED_DETAIL
