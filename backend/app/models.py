"""
Amazon Ads Optimizer — Database Models
Comprehensive schema with proper relationships, indexes, and constraints.
All data persisted to PostgreSQL — no temporary local storage.
"""

import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import (
    String, Text, Float, Integer, BigInteger, Boolean, DateTime,
    JSON, ForeignKey, Index, UniqueConstraint,
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


def _utcnow() -> datetime:
    """Naive UTC now — matches DB columns (TIMESTAMP WITHOUT TIME ZONE)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ══════════════════════════════════════════════════════════════════════
#  ENUMS
# ══════════════════════════════════════════════════════════════════════

class CredentialStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    ERROR = "error"


class OptimizationStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CampaignState(str, enum.Enum):
    ENABLED = "enabled"
    PAUSED = "paused"
    ARCHIVED = "archived"


class IssueSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BidDirection(str, enum.Enum):
    INCREASE = "increase"
    DECREASE = "decrease"


# ══════════════════════════════════════════════════════════════════════
#  CREDENTIALS
# ══════════════════════════════════════════════════════════════════════

class Credential(Base):
    """Amazon Ads API credentials for MCP access."""
    __tablename__ = "credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_id: Mapped[str] = mapped_column(String(512), nullable=False)
    client_secret: Mapped[str] = mapped_column(Text, nullable=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    profile_id: Mapped[str] = mapped_column(String(255), nullable=True)
    account_id: Mapped[str] = mapped_column(String(255), nullable=True)
    region: Mapped[str] = mapped_column(String(10), default="na")
    status: Mapped[str] = mapped_column(String(20), default=CredentialStatus.ACTIVE.value)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    last_tested_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    tools_available: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    accounts: Mapped[list["Account"]] = relationship("Account", back_populates="credential", cascade="all, delete-orphan")
    campaigns: Mapped[list["Campaign"]] = relationship("Campaign", back_populates="credential", cascade="all, delete-orphan")
    ad_groups: Mapped[list["AdGroup"]] = relationship("AdGroup", back_populates="credential", cascade="all, delete-orphan")
    targets: Mapped[list["Target"]] = relationship("Target", back_populates="credential", cascade="all, delete-orphan")
    ads: Mapped[list["Ad"]] = relationship("Ad", back_populates="credential", cascade="all, delete-orphan")
    ad_associations: Mapped[list["AdAssociation"]] = relationship("AdAssociation", back_populates="credential", cascade="all, delete-orphan")
    audit_snapshots: Mapped[list["AuditSnapshot"]] = relationship("AuditSnapshot", back_populates="credential", cascade="all, delete-orphan")
    harvest_configs: Mapped[list["HarvestConfig"]] = relationship("HarvestConfig", back_populates="credential", cascade="all, delete-orphan")
    bid_rules: Mapped[list["BidRule"]] = relationship("BidRule", back_populates="credential", cascade="all, delete-orphan")
    activity_logs: Mapped[list["ActivityLog"]] = relationship("ActivityLog", back_populates="credential", passive_deletes=True)
    reports: Mapped[list["Report"]] = relationship("Report", back_populates="credential", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_credentials_is_default", "is_default"),
        Index("ix_credentials_status", "status"),
    )


# ══════════════════════════════════════════════════════════════════════
#  ACCOUNTS — Discovered Amazon Ads advertiser accounts
# ══════════════════════════════════════════════════════════════════════

class Account(Base):
    """Amazon Ads advertiser accounts discovered via MCP."""
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    amazon_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_name: Mapped[str] = mapped_column(String(512), nullable=True)
    account_type: Mapped[str] = mapped_column(String(100), nullable=True)
    marketplace: Mapped[str] = mapped_column(String(100), nullable=True)
    profile_id: Mapped[str] = mapped_column(String(255), nullable=True)
    account_status: Mapped[str] = mapped_column(String(50), nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    credential: Mapped["Credential"] = relationship("Credential", back_populates="accounts")

    __table_args__ = (
        UniqueConstraint("credential_id", "amazon_account_id", name="uq_account_per_credential"),
        Index("ix_accounts_credential_id", "credential_id"),
        Index("ix_accounts_amazon_account_id", "amazon_account_id"),
    )


# ══════════════════════════════════════════════════════════════════════
#  CAMPAIGNS — Cached campaign data from MCP
# ══════════════════════════════════════════════════════════════════════

class Campaign(Base):
    """Cached Amazon Ads campaign data from MCP queries."""
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(255), nullable=True)  # scopes when multiple profiles share one credential
    amazon_campaign_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_name: Mapped[str] = mapped_column(String(512), nullable=True)
    campaign_type: Mapped[str] = mapped_column(String(100), nullable=True)
    targeting_type: Mapped[str] = mapped_column(String(50), nullable=True)  # auto / manual
    state: Mapped[str] = mapped_column(String(50), nullable=True)
    daily_budget: Mapped[float] = mapped_column(Float, nullable=True)
    start_date: Mapped[str] = mapped_column(String(50), nullable=True)
    end_date: Mapped[str] = mapped_column(String(50), nullable=True)
    spend: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    sales: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    impressions: Mapped[int] = mapped_column(BigInteger, nullable=True, default=0)
    clicks: Mapped[int] = mapped_column(Integer, nullable=True, default=0)
    orders: Mapped[int] = mapped_column(Integer, nullable=True, default=0)
    acos: Mapped[float] = mapped_column(Float, nullable=True)
    roas: Mapped[float] = mapped_column(Float, nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    credential: Mapped["Credential"] = relationship("Credential", back_populates="campaigns")
    ad_groups: Mapped[list["AdGroup"]] = relationship("AdGroup", back_populates="campaign", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("credential_id", "amazon_campaign_id", name="uq_campaign_per_credential"),
        Index("ix_campaigns_credential_id", "credential_id"),
        Index("ix_campaigns_amazon_campaign_id", "amazon_campaign_id"),
        Index("ix_campaigns_state", "state"),
        Index("ix_campaigns_targeting_type", "targeting_type"),
    )


# ══════════════════════════════════════════════════════════════════════
#  AD GROUPS — Cached ad group data
# ══════════════════════════════════════════════════════════════════════

class AdGroup(Base):
    """Cached Amazon Ads ad group data from MCP queries."""
    __tablename__ = "ad_groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True)
    amazon_ad_group_id: Mapped[str] = mapped_column(String(255), nullable=False)
    amazon_campaign_id: Mapped[str] = mapped_column(String(255), nullable=True)
    ad_group_name: Mapped[str] = mapped_column(String(512), nullable=True)
    state: Mapped[str] = mapped_column(String(50), nullable=True)
    default_bid: Mapped[float] = mapped_column(Float, nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    credential: Mapped["Credential"] = relationship("Credential", back_populates="ad_groups")
    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="ad_groups")
    targets: Mapped[list["Target"]] = relationship("Target", back_populates="ad_group", cascade="all, delete-orphan")
    ads: Mapped[list["Ad"]] = relationship("Ad", back_populates="ad_group", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("credential_id", "amazon_ad_group_id", name="uq_adgroup_per_credential"),
        Index("ix_ad_groups_credential_id", "credential_id"),
        Index("ix_ad_groups_campaign_id", "campaign_id"),
        Index("ix_ad_groups_amazon_campaign_id", "amazon_campaign_id"),
    )


# ══════════════════════════════════════════════════════════════════════
#  TARGETS / KEYWORDS — Cached target data
# ══════════════════════════════════════════════════════════════════════

class Target(Base):
    """Cached Amazon Ads targets/keywords from MCP queries."""
    __tablename__ = "targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    ad_group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ad_groups.id", ondelete="CASCADE"), nullable=True)
    amazon_target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    amazon_ad_group_id: Mapped[str] = mapped_column(String(255), nullable=True)
    amazon_campaign_id: Mapped[str] = mapped_column(String(255), nullable=True)
    target_type: Mapped[str] = mapped_column(String(100), nullable=True)  # keyword, product, auto
    expression_type: Mapped[str] = mapped_column(String(100), nullable=True)
    expression_value: Mapped[str] = mapped_column(Text, nullable=True)
    match_type: Mapped[str] = mapped_column(String(50), nullable=True)  # broad, phrase, exact
    state: Mapped[str] = mapped_column(String(50), nullable=True)
    bid: Mapped[float] = mapped_column(Float, nullable=True)
    impressions: Mapped[int] = mapped_column(BigInteger, nullable=True, default=0)
    clicks: Mapped[int] = mapped_column(Integer, nullable=True, default=0)
    spend: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    sales: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    orders: Mapped[int] = mapped_column(Integer, nullable=True, default=0)
    acos: Mapped[float] = mapped_column(Float, nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    credential: Mapped["Credential"] = relationship("Credential", back_populates="targets")
    ad_group: Mapped["AdGroup"] = relationship("AdGroup", back_populates="targets")

    __table_args__ = (
        UniqueConstraint("credential_id", "amazon_target_id", name="uq_target_per_credential"),
        Index("ix_targets_credential_id", "credential_id"),
        Index("ix_targets_ad_group_id", "ad_group_id"),
        Index("ix_targets_amazon_campaign_id", "amazon_campaign_id"),
        Index("ix_targets_state", "state"),
    )


# ══════════════════════════════════════════════════════════════════════
#  ADS — Cached ad data from MCP queries
# ══════════════════════════════════════════════════════════════════════

class Ad(Base):
    """Cached Amazon Ads ad data from MCP queries."""
    __tablename__ = "ads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    ad_group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ad_groups.id", ondelete="CASCADE"), nullable=True)
    amazon_ad_id: Mapped[str] = mapped_column(String(255), nullable=False)
    amazon_ad_group_id: Mapped[str] = mapped_column(String(255), nullable=True)
    amazon_campaign_id: Mapped[str] = mapped_column(String(255), nullable=True)
    ad_name: Mapped[str] = mapped_column(String(512), nullable=True)
    ad_type: Mapped[str] = mapped_column(String(100), nullable=True)  # product_ad, brand_video, etc.
    state: Mapped[str] = mapped_column(String(50), nullable=True)
    asin: Mapped[str] = mapped_column(String(50), nullable=True)
    sku: Mapped[str] = mapped_column(String(255), nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    credential: Mapped["Credential"] = relationship("Credential", back_populates="ads")
    ad_group: Mapped["AdGroup"] = relationship("AdGroup", back_populates="ads")

    __table_args__ = (
        UniqueConstraint("credential_id", "amazon_ad_id", name="uq_ad_per_credential"),
        Index("ix_ads_credential_id", "credential_id"),
        Index("ix_ads_ad_group_id", "ad_group_id"),
        Index("ix_ads_amazon_campaign_id", "amazon_campaign_id"),
        Index("ix_ads_state", "state"),
        Index("ix_ads_asin", "asin"),
    )


# ══════════════════════════════════════════════════════════════════════
#  AD ASSOCIATIONS — Links between ads and ad groups
# ══════════════════════════════════════════════════════════════════════

class AdAssociation(Base):
    """Tracks associations between ads and ad groups."""
    __tablename__ = "ad_associations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    amazon_association_id: Mapped[str] = mapped_column(String(255), nullable=True)
    amazon_ad_id: Mapped[str] = mapped_column(String(255), nullable=False)
    amazon_ad_group_id: Mapped[str] = mapped_column(String(255), nullable=False)
    amazon_campaign_id: Mapped[str] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(50), nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    credential: Mapped["Credential"] = relationship("Credential", back_populates="ad_associations")

    __table_args__ = (
        Index("ix_ad_assoc_credential_id", "credential_id"),
        Index("ix_ad_assoc_ad_id", "amazon_ad_id"),
        Index("ix_ad_assoc_ad_group_id", "amazon_ad_group_id"),
    )


# ══════════════════════════════════════════════════════════════════════
#  REPORTS — Stored report metadata and results
# ══════════════════════════════════════════════════════════════════════

class Report(Base):
    """Amazon Ads reports requested and stored."""
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(100), nullable=False)  # campaign, product, inventory
    ad_product: Mapped[str] = mapped_column(String(100), nullable=True)
    date_range_start: Mapped[str] = mapped_column(String(20), nullable=True)
    date_range_end: Mapped[str] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=OptimizationStatus.PENDING.value)
    report_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    raw_response: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # Relationships
    credential: Mapped["Credential"] = relationship("Credential", back_populates="reports")

    __table_args__ = (
        Index("ix_reports_credential_id", "credential_id"),
        Index("ix_reports_report_type", "report_type"),
        Index("ix_reports_status", "status"),
        Index("ix_reports_created_at", "created_at"),
    )


# ══════════════════════════════════════════════════════════════════════
#  AUDIT SNAPSHOTS — Campaign audit results
# ══════════════════════════════════════════════════════════════════════

class AuditSnapshot(Base):
    """Full audit snapshot containing summary metrics."""
    __tablename__ = "audit_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    snapshot_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    campaigns_count: Mapped[int] = mapped_column(Integer, default=0)
    active_campaigns: Mapped[int] = mapped_column(Integer, default=0)
    paused_campaigns: Mapped[int] = mapped_column(Integer, default=0)
    total_ad_groups: Mapped[int] = mapped_column(Integer, default=0)
    total_targets: Mapped[int] = mapped_column(Integer, default=0)
    total_spend: Mapped[float] = mapped_column(Float, default=0.0)
    total_sales: Mapped[float] = mapped_column(Float, default=0.0)
    avg_acos: Mapped[float] = mapped_column(Float, default=0.0)
    avg_roas: Mapped[float] = mapped_column(Float, default=0.0)
    waste_identified: Mapped[float] = mapped_column(Float, default=0.0)
    issues_count: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=OptimizationStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    credential: Mapped["Credential"] = relationship("Credential", back_populates="audit_snapshots")
    issues: Mapped[list["AuditIssue"]] = relationship("AuditIssue", back_populates="snapshot", cascade="all, delete-orphan")
    opportunities: Mapped[list["AuditOpportunity"]] = relationship("AuditOpportunity", back_populates="snapshot", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_audit_snapshots_credential_id", "credential_id"),
        Index("ix_audit_snapshots_created_at", "created_at"),
        Index("ix_audit_snapshots_status", "status"),
    )


# ══════════════════════════════════════════════════════════════════════
#  AUDIT ISSUES — Queryable issues found during audits
# ══════════════════════════════════════════════════════════════════════

class AuditIssue(Base):
    """Individual issues identified during a campaign audit."""
    __tablename__ = "audit_issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("audit_snapshots.id", ondelete="CASCADE"), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # low, medium, high, critical
    issue_type: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(255), nullable=True)
    campaign_name: Mapped[str] = mapped_column(String(512), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    snapshot: Mapped["AuditSnapshot"] = relationship("AuditSnapshot", back_populates="issues")

    __table_args__ = (
        Index("ix_audit_issues_snapshot_id", "snapshot_id"),
        Index("ix_audit_issues_severity", "severity"),
        Index("ix_audit_issues_issue_type", "issue_type"),
    )


# ══════════════════════════════════════════════════════════════════════
#  AUDIT OPPORTUNITIES — Optimization opportunities from audits
# ══════════════════════════════════════════════════════════════════════

class AuditOpportunity(Base):
    """Optimization opportunities identified during a campaign audit."""
    __tablename__ = "audit_opportunities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("audit_snapshots.id", ondelete="CASCADE"), nullable=False)
    opportunity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    potential_impact: Mapped[str] = mapped_column(String(20), nullable=True)  # low, medium, high
    campaign_id: Mapped[str] = mapped_column(String(255), nullable=True)
    campaign_name: Mapped[str] = mapped_column(String(512), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    snapshot: Mapped["AuditSnapshot"] = relationship("AuditSnapshot", back_populates="opportunities")

    __table_args__ = (
        Index("ix_audit_opportunities_snapshot_id", "snapshot_id"),
        Index("ix_audit_opportunities_type", "opportunity_type"),
    )


# ══════════════════════════════════════════════════════════════════════
#  HARVEST CONFIGS — Keyword harvesting configurations
# ══════════════════════════════════════════════════════════════════════

class HarvestConfig(Base):
    """Configuration for automatic keyword harvesting from auto to manual campaigns."""
    __tablename__ = "harvest_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_campaign_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_campaign_name: Mapped[str] = mapped_column(String(255), nullable=True)
    # Multi-campaign support: [{amazon_campaign_id, campaign_name, targeting_type, state}]
    source_campaigns: Mapped[list] = mapped_column(JSON, nullable=True)
    target_campaign_id: Mapped[str] = mapped_column(String(255), nullable=True)
    target_campaign_name: Mapped[str] = mapped_column(String(255), nullable=True)
    # Target campaign selection: "new" = Amazon creates new, or an existing campaign ID
    target_mode: Mapped[str] = mapped_column(String(50), default="new")  # "new" or "existing"
    # Target campaign detail: {amazon_campaign_id, campaign_name} when targeting existing campaign
    target_campaign_selection: Mapped[dict] = mapped_column(JSON, nullable=True)
    # Negative keyword handling
    negate_in_source: Mapped[bool] = mapped_column(Boolean, default=True)
    sales_threshold: Mapped[float] = mapped_column(Float, default=1.0)
    acos_threshold: Mapped[float] = mapped_column(Float, nullable=True)
    clicks_threshold: Mapped[int] = mapped_column(Integer, nullable=True)
    match_type: Mapped[str] = mapped_column(String(50), nullable=True)  # broad, phrase, exact, or null for all
    lookback_days: Mapped[int] = mapped_column(Integer, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_harvested_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    total_keywords_harvested: Mapped[int] = mapped_column(Integer, default=0)
    total_runs: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=OptimizationStatus.PENDING.value)
    config_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    credential: Mapped["Credential"] = relationship("Credential", back_populates="harvest_configs")
    harvest_runs: Mapped[list["HarvestRun"]] = relationship("HarvestRun", back_populates="config", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_harvest_configs_credential_id", "credential_id"),
        Index("ix_harvest_configs_is_active", "is_active"),
        Index("ix_harvest_configs_status", "status"),
    )


# ══════════════════════════════════════════════════════════════════════
#  HARVEST RUNS — Individual harvest execution records
# ══════════════════════════════════════════════════════════════════════

class HarvestRun(Base):
    """Individual execution of a harvest configuration."""
    __tablename__ = "harvest_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("harvest_configs.id", ondelete="CASCADE"), nullable=False)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=OptimizationStatus.RUNNING.value)
    source_campaign_id: Mapped[str] = mapped_column(String(255), nullable=False)
    target_campaign_id: Mapped[str] = mapped_column(String(255), nullable=True)
    keywords_harvested: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    raw_result: Mapped[dict] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # Relationships
    config: Mapped["HarvestConfig"] = relationship("HarvestConfig", back_populates="harvest_runs")
    harvested_keywords: Mapped[list["HarvestedKeyword"]] = relationship("HarvestedKeyword", back_populates="harvest_run", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_harvest_runs_config_id", "config_id"),
        Index("ix_harvest_runs_credential_id", "credential_id"),
        Index("ix_harvest_runs_status", "status"),
        Index("ix_harvest_runs_started_at", "started_at"),
    )


# ══════════════════════════════════════════════════════════════════════
#  HARVESTED KEYWORDS — Individual keywords moved from auto to manual
# ══════════════════════════════════════════════════════════════════════

class HarvestedKeyword(Base):
    """Individual keyword that was harvested from an auto campaign."""
    __tablename__ = "harvested_keywords"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    harvest_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("harvest_runs.id", ondelete="CASCADE"), nullable=False)
    keyword_text: Mapped[str] = mapped_column(Text, nullable=False)
    match_type: Mapped[str] = mapped_column(String(50), nullable=True)  # broad, phrase, exact
    bid: Mapped[float] = mapped_column(Float, nullable=True)
    source_clicks: Mapped[int] = mapped_column(Integer, nullable=True)
    source_spend: Mapped[float] = mapped_column(Float, nullable=True)
    source_sales: Mapped[float] = mapped_column(Float, nullable=True)
    source_acos: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    harvest_run: Mapped["HarvestRun"] = relationship("HarvestRun", back_populates="harvested_keywords")

    __table_args__ = (
        Index("ix_harvested_keywords_run_id", "harvest_run_id"),
        Index("ix_harvested_keywords_text", "keyword_text"),
    )


# ══════════════════════════════════════════════════════════════════════
#  BID RULES — Bid optimization rule configurations
# ══════════════════════════════════════════════════════════════════════

class BidRule(Base):
    """Bid optimization rules defining ACOS targets and bid boundaries."""
    __tablename__ = "bid_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_ids: Mapped[list] = mapped_column(JSON, nullable=True)
    target_acos: Mapped[float] = mapped_column(Float, nullable=False)
    min_bid: Mapped[float] = mapped_column(Float, default=0.02)
    max_bid: Mapped[float] = mapped_column(Float, default=100.0)
    bid_step: Mapped[float] = mapped_column(Float, default=0.10)
    lookback_days: Mapped[int] = mapped_column(Integer, default=14)
    min_clicks: Mapped[int] = mapped_column(Integer, default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    total_targets_adjusted: Mapped[int] = mapped_column(Integer, default=0)
    total_runs: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=OptimizationStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    credential: Mapped["Credential"] = relationship("Credential", back_populates="bid_rules")
    optimization_runs: Mapped[list["OptimizationRun"]] = relationship("OptimizationRun", back_populates="rule", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_bid_rules_credential_id", "credential_id"),
        Index("ix_bid_rules_is_active", "is_active"),
        Index("ix_bid_rules_status", "status"),
    )


# ══════════════════════════════════════════════════════════════════════
#  OPTIMIZATION RUNS — Individual optimization execution records
# ══════════════════════════════════════════════════════════════════════

class OptimizationRun(Base):
    """Individual execution of a bid optimization rule."""
    __tablename__ = "optimization_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bid_rules.id", ondelete="CASCADE"), nullable=False)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default=OptimizationStatus.RUNNING.value)
    targets_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    targets_adjusted: Mapped[int] = mapped_column(Integer, default=0)
    bid_increases: Mapped[int] = mapped_column(Integer, default=0)
    bid_decreases: Mapped[int] = mapped_column(Integer, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, default=0)
    target_acos: Mapped[float] = mapped_column(Float, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    summary_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # Relationships
    rule: Mapped["BidRule"] = relationship("BidRule", back_populates="optimization_runs")
    bid_changes: Mapped[list["BidChange"]] = relationship("BidChange", back_populates="optimization_run", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_optimization_runs_rule_id", "rule_id"),
        Index("ix_optimization_runs_credential_id", "credential_id"),
        Index("ix_optimization_runs_status", "status"),
        Index("ix_optimization_runs_started_at", "started_at"),
    )


# ══════════════════════════════════════════════════════════════════════
#  BID CHANGES — Individual bid adjustments from optimization runs
# ══════════════════════════════════════════════════════════════════════

class BidChange(Base):
    """Individual bid change made during an optimization run."""
    __tablename__ = "bid_changes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    optimization_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("optimization_runs.id", ondelete="CASCADE"), nullable=False)
    amazon_target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    amazon_campaign_id: Mapped[str] = mapped_column(String(255), nullable=True)
    previous_bid: Mapped[float] = mapped_column(Float, nullable=False)
    new_bid: Mapped[float] = mapped_column(Float, nullable=False)
    bid_change: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)  # increase / decrease
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    current_acos: Mapped[float] = mapped_column(Float, nullable=True)
    clicks: Mapped[int] = mapped_column(Integer, nullable=True)
    spend: Mapped[float] = mapped_column(Float, nullable=True)
    sales: Mapped[float] = mapped_column(Float, nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    optimization_run: Mapped["OptimizationRun"] = relationship("OptimizationRun", back_populates="bid_changes")

    __table_args__ = (
        Index("ix_bid_changes_run_id", "optimization_run_id"),
        Index("ix_bid_changes_target_id", "amazon_target_id"),
        Index("ix_bid_changes_direction", "direction"),
        Index("ix_bid_changes_applied", "applied"),
    )


# ══════════════════════════════════════════════════════════════════════
#  ACTIVITY LOG — Comprehensive action logging
# ══════════════════════════════════════════════════════════════════════

class ActivityLog(Base):
    """Logs all actions taken in the system for audit trail."""
    __tablename__ = "activity_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)  # settings, audit, harvest, optimizer, accounts
    description: Mapped[str] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=True)  # credential, snapshot, config, rule, run
    entity_id: Mapped[str] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="success")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    credential: Mapped["Credential"] = relationship("Credential", back_populates="activity_logs")

    __table_args__ = (
        Index("ix_activity_log_credential_id", "credential_id"),
        Index("ix_activity_log_category", "category"),
        Index("ix_activity_log_action", "action"),
        Index("ix_activity_log_created_at", "created_at"),
        Index("ix_activity_log_entity", "entity_type", "entity_id"),
    )


# ══════════════════════════════════════════════════════════════════════
#  PENDING CHANGES — Approval queue before pushing to Amazon Ads
# ══════════════════════════════════════════════════════════════════════

class ChangeStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    FAILED = "failed"


class PendingChange(Base):
    """
    Approval queue: all changes to Amazon Ads must be reviewed here first.
    Supports bid changes, budget updates, campaign state changes, keyword adds, etc.
    Nothing is pushed to Amazon Ads until explicitly approved.
    """
    __tablename__ = "pending_changes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(255), nullable=True)  # Account scope when change was created
    change_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # change_type values: bid_update, budget_update, campaign_state, keyword_add,
    #                      keyword_remove, campaign_create, target_create, harvest
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # entity_type: campaign, ad_group, target, keyword
    entity_id: Mapped[str] = mapped_column(String(255), nullable=True)
    entity_name: Mapped[str] = mapped_column(String(512), nullable=True)
    campaign_id: Mapped[str] = mapped_column(String(255), nullable=True)
    campaign_name: Mapped[str] = mapped_column(String(512), nullable=True)

    # Current vs proposed
    current_value: Mapped[str] = mapped_column(Text, nullable=True)
    proposed_value: Mapped[str] = mapped_column(Text, nullable=True)
    change_detail: Mapped[dict] = mapped_column(JSON, nullable=True)
    # Full payload needed to execute via MCP
    mcp_payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    # AI reasoning (if generated by AI)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    # source: manual, ai_insight, ai_optimizer, bid_optimizer, harvester
    ai_reasoning: Mapped[str] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    estimated_impact: Mapped[str] = mapped_column(Text, nullable=True)

    # Approval workflow
    status: Mapped[str] = mapped_column(String(20), default=ChangeStatus.PENDING.value)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    apply_result: Mapped[dict] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)

    # Batch grouping (e.g., all changes from one optimization run)
    batch_id: Mapped[str] = mapped_column(String(255), nullable=True)
    batch_label: Mapped[str] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_pending_changes_credential_id", "credential_id"),
        Index("ix_pending_changes_status", "status"),
        Index("ix_pending_changes_change_type", "change_type"),
        Index("ix_pending_changes_source", "source"),
        Index("ix_pending_changes_batch_id", "batch_id"),
        Index("ix_pending_changes_created_at", "created_at"),
        Index("ix_pending_changes_entity", "entity_type", "entity_id"),
    )


# ══════════════════════════════════════════════════════════════════════
#  AI CONVERSATIONS — Chat history and AI interaction tracking
# ══════════════════════════════════════════════════════════════════════

class AIConversation(Base):
    """Tracks AI assistant conversations for context continuity."""
    __tablename__ = "ai_conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=True)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    # messages: [{"role": "user"|"assistant", "content": "...", "timestamp": "..."}]
    context_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    # Cached account data for AI context
    changes_proposed: Mapped[int] = mapped_column(Integer, default=0)
    changes_approved: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_ai_conversations_credential_id", "credential_id"),
        Index("ix_ai_conversations_is_active", "is_active"),
        Index("ix_ai_conversations_created_at", "created_at"),
    )


# ══════════════════════════════════════════════════════════════════════
#  CAMPAIGN PERFORMANCE DAILY — Historical per-campaign daily metrics
# ══════════════════════════════════════════════════════════════════════

class CampaignPerformanceDaily(Base):
    """
    One row per campaign per date. Stores daily performance metrics so that
    date-range reports, trend charts, and period comparisons can be served
    entirely from the database without calling the Amazon Ads API.
    """
    __tablename__ = "campaign_performance_daily"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(255), nullable=True)  # scopes when multiple profiles share one credential
    amazon_campaign_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_name: Mapped[str] = mapped_column(String(512), nullable=True)
    campaign_type: Mapped[str] = mapped_column(String(100), nullable=True)
    targeting_type: Mapped[str] = mapped_column(String(50), nullable=True)
    state: Mapped[str] = mapped_column(String(50), nullable=True)
    date: Mapped[str] = mapped_column(String(25), nullable=False)  # YYYY-MM-DD or range key

    # Performance metrics
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    sales: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    orders: Mapped[int] = mapped_column(Integer, default=0)
    acos: Mapped[float] = mapped_column(Float, nullable=True)
    roas: Mapped[float] = mapped_column(Float, nullable=True)
    ctr: Mapped[float] = mapped_column(Float, nullable=True)
    cpc: Mapped[float] = mapped_column(Float, nullable=True)
    cvr: Mapped[float] = mapped_column(Float, nullable=True)
    daily_budget: Mapped[float] = mapped_column(Float, nullable=True)

    # Metadata
    source: Mapped[str] = mapped_column(String(50), default="mcp_report")  # mcp_report | audit | sync
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("credential_id", "amazon_campaign_id", "date", name="uq_campaign_perf_daily"),
        Index("ix_cpd_credential_id", "credential_id"),
        Index("ix_cpd_campaign_id", "amazon_campaign_id"),
        Index("ix_cpd_date", "date"),
        Index("ix_cpd_credential_date", "credential_id", "date"),
    )


# ══════════════════════════════════════════════════════════════════════
#  ACCOUNT PERFORMANCE DAILY — Historical aggregated daily totals
# ══════════════════════════════════════════════════════════════════════

class AccountPerformanceDaily(Base):
    """
    One row per account (credential) per date. Aggregated totals across all
    campaigns so that account-level trend charts and comparisons are fast.
    """
    __tablename__ = "account_performance_daily"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(255), nullable=True)  # scopes when multiple profiles share one credential
    date: Mapped[str] = mapped_column(String(25), nullable=False)  # YYYY-MM-DD or range key

    # Aggregate metrics
    total_spend: Mapped[float] = mapped_column(Float, default=0.0)
    total_sales: Mapped[float] = mapped_column(Float, default=0.0)
    total_impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    total_clicks: Mapped[int] = mapped_column(Integer, default=0)
    total_orders: Mapped[int] = mapped_column(Integer, default=0)
    avg_acos: Mapped[float] = mapped_column(Float, nullable=True)
    avg_roas: Mapped[float] = mapped_column(Float, nullable=True)
    avg_ctr: Mapped[float] = mapped_column(Float, nullable=True)
    avg_cpc: Mapped[float] = mapped_column(Float, nullable=True)
    avg_cvr: Mapped[float] = mapped_column(Float, nullable=True)

    # Counts
    total_campaigns: Mapped[int] = mapped_column(Integer, default=0)
    active_campaigns: Mapped[int] = mapped_column(Integer, default=0)
    paused_campaigns: Mapped[int] = mapped_column(Integer, default=0)

    # Metadata
    source: Mapped[str] = mapped_column(String(50), default="mcp_report")
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("credential_id", "date", name="uq_account_perf_daily"),
        Index("ix_apd_credential_id", "credential_id"),
        Index("ix_apd_date", "date"),
        Index("ix_apd_credential_date", "credential_id", "date"),
    )


# ══════════════════════════════════════════════════════════════════════
#  SEARCH TERM PERFORMANCE — Customer search queries from reports
# ══════════════════════════════════════════════════════════════════════

class SearchTermPerformance(Base):
    """
    Stores search term performance data from Amazon Ads search term reports.
    Each row represents a unique (credential, search_term, campaign, ad_group, date)
    combination with traffic and conversion metrics.
    """
    __tablename__ = "search_term_performance"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(255), nullable=True)  # scopes data when multiple profiles share one credential

    # Search term and targeting info
    search_term: Mapped[str] = mapped_column(Text, nullable=False)
    keyword: Mapped[str] = mapped_column(Text, nullable=True)           # matched keyword/target
    keyword_id: Mapped[str] = mapped_column(String(255), nullable=True)
    keyword_type: Mapped[str] = mapped_column(String(100), nullable=True)  # BROAD, PHRASE, EXACT, TARGETING_EXPRESSION
    match_type: Mapped[str] = mapped_column(String(50), nullable=True)
    targeting: Mapped[str] = mapped_column(Text, nullable=True)         # full targeting expression

    # Campaign / ad group context
    amazon_campaign_id: Mapped[str] = mapped_column(String(255), nullable=True)
    campaign_name: Mapped[str] = mapped_column(String(512), nullable=True)
    amazon_ad_group_id: Mapped[str] = mapped_column(String(255), nullable=True)
    ad_group_name: Mapped[str] = mapped_column(String(512), nullable=True)

    # Date
    date: Mapped[str] = mapped_column(String(25), nullable=False)  # YYYY-MM-DD or SUMMARY

    # Traffic metrics
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)           # spend
    ctr: Mapped[float] = mapped_column(Float, nullable=True)
    cpc: Mapped[float] = mapped_column(Float, nullable=True)

    # Conversion metrics (7-day attribution window)
    purchases: Mapped[int] = mapped_column(Integer, default=0)
    sales: Mapped[float] = mapped_column(Float, default=0.0)
    units_sold: Mapped[int] = mapped_column(Integer, default=0)
    acos: Mapped[float] = mapped_column(Float, nullable=True)
    roas: Mapped[float] = mapped_column(Float, nullable=True)

    # Ad product (SPONSORED_PRODUCTS, SPONSORED_BRANDS)
    ad_product: Mapped[str] = mapped_column(String(100), nullable=True)

    # Metadata
    report_date_start: Mapped[str] = mapped_column(String(25), nullable=True)
    report_date_end: Mapped[str] = mapped_column(String(25), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_stp_credential_id", "credential_id"),
        Index("ix_stp_profile_id", "profile_id"),
        Index("ix_stp_credential_profile", "credential_id", "profile_id"),
        Index("ix_stp_search_term", "search_term"),
        Index("ix_stp_campaign_id", "amazon_campaign_id"),
        Index("ix_stp_date", "date"),
        Index("ix_stp_credential_date", "credential_id", "date"),
        Index("ix_stp_clicks", "clicks"),
        Index("ix_stp_purchases", "purchases"),
    )


# ══════════════════════════════════════════════════════════════════════
#  USERS — App users for login & access control
# ══════════════════════════════════════════════════════════════════════

class User(Base):
    """App user for login and access control."""
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(50), default="user")  # admin, user
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_users_email", "email"),
        Index("ix_users_role", "role"),
    )


# ══════════════════════════════════════════════════════════════════════
#  INVITATIONS — Invite new users by email
# ══════════════════════════════════════════════════════════════════════

class Invitation(Base):
    """Invitation to register. Token is single-use, expires after 7 days."""
    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), default="user")
    invited_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, accepted, expired
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_invitations_token", "token"),
        Index("ix_invitations_email", "email"),
        Index("ix_invitations_status", "status"),
    )


# ══════════════════════════════════════════════════════════════════════
#  APP SETTINGS — Application-wide settings (LLM configuration, etc.)
# ══════════════════════════════════════════════════════════════════════

class AppSettings(Base):
    """Application-wide settings. Single row, key-value style."""
    __tablename__ = "app_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # default_llm_id: "openai:gpt-5.2" or "anthropic:claude-sonnet-4-20250514"
    default_llm_id: Mapped[str] = mapped_column(String(128), nullable=True)
    # enabled_llms: [{"provider": "openai", "model": "gpt-5.2", "label": "GPT-5.2"}]
    enabled_llms: Mapped[list] = mapped_column(JSON, default=list)
    # Encrypted API keys (stored from Settings UI; env vars take precedence if set)
    openai_api_key: Mapped[str] = mapped_column(Text, nullable=True)
    anthropic_api_key: Mapped[str] = mapped_column(Text, nullable=True)
    # PA-API (Product Advertising API) for product images — access_key, secret_key, partner_tag
    paapi_access_key: Mapped[str] = mapped_column(Text, nullable=True)
    paapi_secret_key: Mapped[str] = mapped_column(Text, nullable=True)
    paapi_partner_tag: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
