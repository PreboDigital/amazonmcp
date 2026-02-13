# Amazon Ads Optimizer — App Review Report

**Date:** February 13, 2025  
**Scope:** Full app review for bugs, errors, and feature improvements based on Amazon Ads MCP documentation.

---

## 1. Bugs Fixed

### 1.1 SQL LIKE Wildcard Bug (Fixed)

**Location:** `backend/app/services/reporting_service.py` — `find_encompassing_range_data()`

**Issue:** `CampaignPerformanceDaily.date.contains("__")` was used to find range-key rows (e.g. `2026-02-01__2026-02-12`). In SQL `LIKE`, the underscore `_` is a wildcard that matches any single character. So `LIKE '%__%'` would incorrectly match almost any string with 2+ characters instead of the literal `__`.

**Fix:** Replaced with `func.strpos(CampaignPerformanceDaily.date, "__") > 0`, which correctly matches the literal double-underscore separator.

---

## 2. Feature Improvements (MCP Documentation Alignment)

### 2.1 MCP Client Enhancements (Implemented)

Based on the documentation in `/documentation/`:

| Feature | Status | Notes |
|---------|--------|-------|
| `query_account_links` | ✅ Added | Account Management: Manager/advertiser account link queries |
| `delete_report` | ✅ Added | Reports: Delete reports by ID |
| `create_product_report` | ✅ Added | Reports: Product report creation |
| `create_inventory_report` | ✅ Added | Reports: Inventory report creation |

### 2.2 Audit Service Refactor

- Replaced direct `call_tool` usage for product/inventory reports with the new MCP client convenience methods for consistency and maintainability.

---

## 3. Documentation-Based Feature Gaps (Recommendations)

### 3.1 Stream Subscriptions (Not Implemented)

The **stream-Amazon Ads Advanced Tools Center** documentation describes:

- `create_adsp_purchase_overview_subsc` — ADSP purchase overview Stream subscription
- `create_adsp_traffic_overview_subscript` — ADSP traffic overview Stream subscription
- `create_subscription`, `delete_subscription`, `list_subscription`, `retrieve_subscription`, `update_subscription`

**Recommendation:** Add Stream Subscriptions support for real-time purchase and traffic data if ADSP (Amazon DSP) use cases are required.

### 3.2 Account Management (Partially Implemented)

Documented but not yet exposed in the app:

- `create_terms_token` / `get_terms_token` — Advertising terms acceptance
- `create_user_invitations` / `list_user_invitations` / `get_user_invitation` / `redeem_user_invitation` / `update_user_invitations` — User invitation flows
- `update_advertiser_account` — Update advertiser account
- `update_account_currency` / `update_account_name` / `update_account_timezone` — Account settings

**Recommendation:** Add these to the MCP client and expose via a settings/account management UI when multi-user or account configuration features are needed.

### 3.3 Campaign Management (Well Covered)

The app already uses:

- Ad, Ad Association, Ad Group, Campaign, Target CRUD
- `add_country_campaign`, `create_campaign_harvest_targets`, `create_singleshot_sp_campaign`
- `update_target_bid`, `update_campaign_budget`, `update_campaign_state`

All documented campaign management tools are supported.

### 3.4 Reports (Well Covered)

- `create_campaign_report`, `create_product_report`, `create_inventory_report`, `create_report`
- `retrieve_report`, `delete_report`

Search term reports use the v3 API directly (as noted in the MCP client) because the MCP reporting tool does not support search term dimensions.

---

## 4. Potential Issues (Not Fixed — Low Risk)

### 4.1 Reporting Comparison Block — `service` Variable Scope

**Location:** `backend/app/routers/reporting.py` — comparison period block

**Observation:** `service` is used when `report_source == "amazon_ads_api"`. It is only created inside the main try block. If the try block fails before `service` is created, a `NameError` could occur.

**Analysis:** `report_source == "amazon_ads_api"` is only set when the MCP report succeeds, which requires `service` to have been created. So in practice this path is safe. No change made.

### 4.2 `accessRequestedAccount` vs `accessRequestedAccounts`

- **Campaign Management / Account Management:** Use `accessRequestedAccount` (singular) in request bodies.
- **Reports:** Use `accessRequestedAccounts` (plural) in request bodies.

The codebase correctly uses the appropriate form per API.

---

## 5. Code Quality Notes

- **MCP tool naming:** All tool names follow the `{resource}-{action}` pattern (e.g. `campaign_management-query_campaign`, `reporting-create_campaign_report`).
- **Pagination:** `_paginated_query` in `mcp_client.py` correctly follows `nextToken` for multi-page results.
- **Error handling:** `MCPError` is raised for validation and connection failures; callers receive clear error messages.
- **Region support:** NA, EU, FE regions are supported via `REGION_URLS`.

---

## 6. Summary

| Category | Count |
|----------|-------|
| Bugs fixed | 1 |
| Features added | 4 (MCP client) |
| Refactors | 1 (audit service) |
| Documentation gaps identified | 2 (Stream Subscriptions, full Account Management) |

The app is well-aligned with the Amazon Ads MCP documentation. The main fix was the SQL `LIKE` wildcard bug in range-key lookups. Additional MCP features were added for account links, report deletion, and product/inventory reports. Stream Subscriptions and full Account Management remain as future enhancement options.
