"""Build weekly account digest HTML for email (per user, all credentials)."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AccountPerformanceDaily,
    Credential,
    PendingChange,
    User,
)
from app.services.data_freshness import build_tables_and_jobs_freshness, overall_freshness_status
from app.utils import utcnow

logger = logging.getLogger(__name__)


async def _sum_spend_apd(
    db: AsyncSession,
    credential_id,
    profile_id: Optional[str],
    start_d: str,
    end_d: str,
) -> float:
    q = (
        select(sa_func.coalesce(sa_func.sum(AccountPerformanceDaily.total_spend), 0.0))
        .where(
            AccountPerformanceDaily.credential_id == credential_id,
            AccountPerformanceDaily.date >= start_d,
            AccountPerformanceDaily.date <= end_d,
        )
    )
    if profile_id is not None:
        q = q.where(AccountPerformanceDaily.profile_id == profile_id)
    else:
        q = q.where(AccountPerformanceDaily.profile_id.is_(None))
    return float((await db.execute(q)).scalar() or 0.0)


async def _pending_count(db: AsyncSession, credential_id) -> int:
    q = (
        select(sa_func.count())
        .select_from(PendingChange)
        .where(
            PendingChange.credential_id == credential_id,
            PendingChange.status == "pending",
        )
    )
    return int((await db.execute(q)).scalar() or 0)


async def build_weekly_digest_html(
    db: AsyncSession,
    *,
    user: User,
    app_base_url: str,
) -> Optional[str]:
    """Return HTML body or None when there is nothing to report."""
    creds_result = await db.execute(select(Credential).order_by(Credential.is_default.desc(), Credential.created_at))
    creds = creds_result.scalars().all()
    if not creds:
        return None

    now = utcnow().replace(tzinfo=None)
    end_cur = now.date()
    start_cur = end_cur - timedelta(days=6)
    end_prev = start_cur - timedelta(days=1)
    start_prev = end_prev - timedelta(days=6)
    cur_s = start_cur.isoformat()
    cur_e = end_cur.isoformat()
    prev_s = start_prev.isoformat()
    prev_e = end_prev.isoformat()

    sections: list[str] = []
    for cred in creds:
        profile_id = cred.profile_id
        pend = await _pending_count(db, cred.id)
        cur_spend = await _sum_spend_apd(db, cred.id, profile_id, cur_s, cur_e)
        prev_spend = await _sum_spend_apd(db, cred.id, profile_id, prev_s, prev_e)
        core = await build_tables_and_jobs_freshness(db, cred, profile_id)
        overall = overall_freshness_status(core["tables"])
        worst = []
        for name, t in core["tables"].items():
            if t.get("staleness") in ("stale", "never", "warn"):
                worst.append(f"{name}: {t.get('staleness')}")
        worst_s = ", ".join(worst[:4]) if worst else "all fresh"

        delta = cur_spend - prev_spend
        delta_pct = (delta / prev_spend * 100.0) if prev_spend else 0.0

        sections.append(
            f"""
            <div style="margin-bottom:20px;padding:12px;border:1px solid #e2e8f0;border-radius:8px;">
              <h3 style="margin:0 0 8px 0;color:#1e293b;">{cred.name}</h3>
              <p style="margin:4px 0;font-size:14px;"><strong>Spend (last 7d):</strong> ${cur_spend:,.2f}
                 vs prior week ${prev_spend:,.2f}
                 ({delta:+,.2f}, {delta_pct:+.1f}%)</p>
              <p style="margin:4px 0;font-size:14px;"><strong>Pending approvals:</strong> {pend}</p>
              <p style="margin:4px 0;font-size:14px;"><strong>Data freshness:</strong> {overall} — {worst_s}</p>
              <p style="margin:4px 0;font-size:13px;color:#64748b;">Token expires: {cred.token_expires_at or "—"}</p>
            </div>
            """
        )

    base = (app_base_url or "").rstrip("/")
    dash = f"{base}/overview" if base else "/overview"

    return f"""
    <div style="font-family:system-ui,sans-serif;max-width:640px;">
      <p>Hi {user.name or user.email},</p>
      <p>Here is your weekly Amazon Ads Optimizer digest ({cur_s} — {cur_e}).</p>
      {"".join(sections)}
      <p><a href="{dash}" style="color:#6366f1;font-weight:600;">Open dashboard</a></p>
      <p style="font-size:12px;color:#94a3b8;">Disable in Settings → Notifications.</p>
    </div>
    """
