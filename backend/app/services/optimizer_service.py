"""
Optimizer Service — Bid optimization engine.
Calculates optimal bids based on ACOS targets and applies them.
"""

import logging
from typing import Optional
from app.mcp_client import AmazonAdsMCP

logger = logging.getLogger(__name__)


class OptimizerService:
    def __init__(self, client: AmazonAdsMCP, advertiser_account_id: str = None):
        self.client = client
        self.advertiser_account_id = advertiser_account_id

    async def optimize_bids(
        self,
        campaign_ids: Optional[list[str]] = None,
        target_acos: float = 30.0,
        min_bid: float = 0.02,
        max_bid: float = 100.0,
        bid_step: float = 0.10,
        min_clicks: int = 10,
        dry_run: bool = True,
    ) -> dict:
        """
        Analyze targets and calculate optimal bids based on ACOS target.

        Algorithm:
        - For targets with ACOS > target: decrease bid by step amount
        - For targets with ACOS < target and good conversions: increase bid
        - For targets with spend but no sales (after sufficient clicks): decrease bid
        - Respect min/max bid boundaries

        Args:
            campaign_ids: Optional list of campaign IDs to optimize (None = all)
            target_acos: Desired ACOS percentage
            min_bid: Minimum bid amount
            max_bid: Maximum bid amount
            bid_step: Amount to adjust bids by
            min_clicks: Minimum clicks before making bid decisions
            dry_run: If True, only preview changes without applying
        """
        logger.info(f"Starting bid optimization (dry_run={dry_run})")

        # Step 1: Get all targets
        if campaign_ids:
            all_targets = []
            for cid in campaign_ids:
                targets = await self.client.query_targets(campaign_id=cid)
                all_targets.append({"campaign_id": cid, "targets": targets})
        else:
            targets_data = await self.client.query_targets()
            all_targets = [{"campaign_id": "all", "targets": targets_data}]

        # Step 2: Get performance report
        from datetime import date, timedelta
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=30)).isoformat()
        report_config = {
            "reports": [{
                "format": "GZIP_JSON",
                "periods": [{"datePeriod": {"startDate": start, "endDate": end}}],
            }],
        }
        try:
            report = await self.client.create_campaign_report(
                report_config,
                advertiser_account_id=self.advertiser_account_id,
            )
        except Exception as e:
            logger.warning(f"Could not generate performance report: {e}")
            report = {}

        # Step 3: Calculate bid adjustments
        adjustments = self._calculate_adjustments(
            all_targets=all_targets,
            target_acos=target_acos,
            min_bid=min_bid,
            max_bid=max_bid,
            bid_step=bid_step,
            min_clicks=min_clicks,
        )

        # Step 4: Apply if not dry run
        applied_count = 0
        if not dry_run and adjustments["changes"]:
            try:
                bid_updates = [
                    {
                        "targetId": change["target_id"],
                        "bid": change["new_bid"],
                    }
                    for change in adjustments["changes"]
                ]
                await self.client.update_target_bids(bid_updates)
                applied_count = len(bid_updates)
                logger.info(f"Applied {applied_count} bid changes")
            except Exception as e:
                logger.error(f"Failed to apply bid changes: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "preview": adjustments,
                }

        return {
            "status": "applied" if not dry_run else "preview",
            "dry_run": dry_run,
            "targets_analyzed": adjustments["analyzed"],
            "targets_adjusted": applied_count if not dry_run else len(adjustments["changes"]),
            "changes": adjustments["changes"],
            "summary": adjustments["summary"],
            "report": report,
            "_raw_targets": all_targets,  # raw MCP data for DB caching
        }

    def _calculate_adjustments(
        self,
        all_targets: list[dict],
        target_acos: float,
        min_bid: float,
        max_bid: float,
        bid_step: float,
        min_clicks: int,
    ) -> dict:
        """Calculate bid adjustments for each target based on performance."""
        changes = []
        analyzed = 0
        increases = 0
        decreases = 0
        unchanged = 0

        for group in all_targets:
            target_list = self._extract_targets(group.get("targets", {}))

            for target in target_list:
                analyzed += 1
                target_id = target.get("targetId") or target.get("id")
                # Extract bid from nested MCP format (bid can be dict or float)
                raw_bid = target.get("bid") or target.get("defaultBid")
                if isinstance(raw_bid, dict):
                    raw_bid = raw_bid.get("value") or raw_bid.get("monetaryBid", {}).get("value")
                current_bid = self._safe_float(raw_bid, 0)
                # Performance metrics may not be in MCP query response (comes from reports)
                # but we still try to extract them for DB-cached targets
                clicks = self._safe_int(target.get("clicks"), 0)
                spend = self._safe_float(target.get("spend") or target.get("cost"), 0)
                sales = self._safe_float(target.get("sales") or target.get("attributedSales"), 0)
                state = target.get("state", "").upper()

                # Skip non-enabled targets
                if state not in ("ENABLED", ""):
                    unchanged += 1
                    continue

                if not target_id or current_bid <= 0:
                    unchanged += 1
                    continue

                # Calculate current ACOS
                current_acos = (spend / sales * 100) if sales > 0 else None

                # Determine action
                new_bid = current_bid
                reason = ""

                if clicks >= min_clicks:
                    if current_acos is not None:
                        if current_acos > target_acos * 1.2:
                            # ACOS too high — decrease bid
                            new_bid = max(current_bid - bid_step, min_bid)
                            reason = f"ACOS {current_acos:.1f}% > target {target_acos}% (high)"
                            decreases += 1
                        elif current_acos > target_acos:
                            # ACOS slightly above target — small decrease
                            new_bid = max(current_bid - (bid_step * 0.5), min_bid)
                            reason = f"ACOS {current_acos:.1f}% slightly above target {target_acos}%"
                            decreases += 1
                        elif current_acos < target_acos * 0.7:
                            # ACOS well below target — increase bid to win more
                            new_bid = min(current_bid + bid_step, max_bid)
                            reason = f"ACOS {current_acos:.1f}% well below target (room to grow)"
                            increases += 1
                        else:
                            unchanged += 1
                            continue
                    else:
                        # Spend with no sales — likely waste
                        if spend > 0:
                            new_bid = max(current_bid - bid_step, min_bid)
                            reason = f"No sales after {clicks} clicks (${spend:.2f} spent)"
                            decreases += 1
                        else:
                            unchanged += 1
                            continue
                else:
                    unchanged += 1
                    continue

                # Only record if bid actually changed
                if abs(new_bid - current_bid) >= 0.01:
                    changes.append({
                        "target_id": target_id,
                        "current_bid": round(current_bid, 2),
                        "new_bid": round(new_bid, 2),
                        "change": round(new_bid - current_bid, 2),
                        "direction": "increase" if new_bid > current_bid else "decrease",
                        "reason": reason,
                        "current_acos": round(current_acos, 1) if current_acos else None,
                        "clicks": clicks,
                        "spend": round(spend, 2),
                        "sales": round(sales, 2),
                    })

        return {
            "analyzed": analyzed,
            "changes": changes,
            "summary": {
                "total_analyzed": analyzed,
                "increases": increases,
                "decreases": decreases,
                "unchanged": unchanged,
                "total_changes": len(changes),
                "target_acos": target_acos,
            },
        }

    @staticmethod
    def _extract_targets(data) -> list:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ["targets", "result", "results", "items"]:
                if key in data and isinstance(data[key], list):
                    return data[key]
        return []

    @staticmethod
    def _safe_float(val, default=0.0) -> float:
        try:
            return float(val) if val is not None else default
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _safe_int(val, default=0) -> int:
        try:
            return int(val) if val is not None else default
        except (ValueError, TypeError):
            return default
