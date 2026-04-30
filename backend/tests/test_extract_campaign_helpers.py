"""Regression tests for campaign sync extraction helpers (Railway log shapes)."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.utils import extract_ad_asin_sku, extract_target_expression  # noqa: E402


def test_extract_keyword_target_numeric_only():
    """Negative keyword '7' — must not rely on _is_keyword_like (rejects isdigit)."""
    tgt = {
        "adGroupId": "90255404026600",
        "adProduct": "SPONSORED_PRODUCTS",
        "campaignId": "156151191212950",
        "negative": True,
        "state": "ENABLED",
        "targetDetails": {"keywordTarget": {"keyword": "7", "matchType": "PHRASE"}},
        "targetId": "119353523365006",
        "targetLevel": "AD_GROUP",
        "targetType": "KEYWORD",
    }
    assert extract_target_expression(tgt) == "7"


def test_extract_product_creative_resolved_asin():
    """SP product ad with productCreative.advertisedProduct (SKU + resolved ASIN)."""
    ad = {
        "adGroupId": "22630256899037",
        "adId": "92348804350483",
        "adProduct": "SPONSORED_PRODUCTS",
        "adType": "PRODUCT_AD",
        "campaignId": "126539342846511",
        "creative": {
            "productCreative": {
                "productCreativeSettings": {
                    "advertisedProduct": {
                        "productId": "3299-5",
                        "productIdType": "SKU",
                        "resolvedProductId": "B00WT3PJ0W",
                        "resolvedProductIdType": "ASIN",
                    }
                }
            }
        },
        "state": "ENABLED",
    }
    asin, sku = extract_ad_asin_sku(ad)
    assert asin == "B00WT3PJ0W"
    assert sku == "3299-5"
