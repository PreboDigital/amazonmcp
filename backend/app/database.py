"""
Database configuration and session management.
Uses PostgreSQL via asyncpg with SQLAlchemy 2 async engine.
"""

import logging
import ssl
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


def _get_connect_args():
    """Enable SSL for Railway Postgres (uses rlwy.net proxy with SSL)."""
    url = settings.database_url
    args = {"timeout": 30}  # Fail fast if DB unreachable
    if "rlwy.net" in url:
        # Railway Postgres requires SSL; use context that accepts self-signed certs
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        args["ssl"] = ctx
    return args


engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    connect_args=_get_connect_args(),
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """Dependency that provides a database session with auto-commit/rollback."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """
    Create all tables defined in models.
    Uses create_all which is safe — it only creates tables that don't exist yet.
    Also adds missing columns to existing tables for smooth development.
    """
    # Import models to ensure they are registered with Base.metadata
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        # Create all tables
        await conn.run_sync(Base.metadata.create_all)

        # Add missing columns to existing tables (dev convenience)
        await _add_missing_columns(conn)

        logger.info(f"Database initialized with {len(Base.metadata.tables)} tables: "
                     f"{', '.join(Base.metadata.tables.keys())}")


async def _add_missing_columns(conn):
    """Add any missing columns to existing tables. Safe to run repeatedly."""
    column_additions = [
        # HarvestConfig new columns
        ("harvest_configs", "source_campaigns", "JSONB"),
        ("harvest_configs", "clicks_threshold", "INTEGER"),
        ("harvest_configs", "match_type", "VARCHAR(50)"),
        ("harvest_configs", "lookback_days", "INTEGER DEFAULT 30"),
        ("harvest_configs", "target_mode", "VARCHAR(50) DEFAULT 'new'"),
        ("harvest_configs", "target_campaign_selection", "JSONB"),
        ("harvest_configs", "negate_in_source", "BOOLEAN DEFAULT true"),
        # Target new columns
        ("targets", "updated_at", "TIMESTAMP"),
        # Profile scoping for multi-account credentials
        ("search_term_performance", "profile_id", "VARCHAR(255)"),
        ("campaigns", "profile_id", "VARCHAR(255)"),
        ("campaign_performance_daily", "profile_id", "VARCHAR(255)"),
        ("account_performance_daily", "profile_id", "VARCHAR(255)"),
        # App settings API keys (encrypted)
        ("app_settings", "openai_api_key", "TEXT"),
        ("app_settings", "anthropic_api_key", "TEXT"),
        # PA-API for product images
        ("app_settings", "paapi_access_key", "TEXT"),
        ("app_settings", "paapi_secret_key", "TEXT"),
        ("app_settings", "paapi_partner_tag", "VARCHAR(64)"),
        # PendingChange: account scope for correct MCP apply
        ("pending_changes", "profile_id", "VARCHAR(255)"),
    ]
    for table, column, col_type in column_additions:
        try:
            await conn.execute(text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}"
            ))
        except Exception as e:
            logger.debug(f"Column addition skipped ({table}.{column}): {e}")


async def drop_and_recreate_db():
    """
    Drop all tables and recreate them. USE WITH CAUTION — destroys all data.
    Only allowed in development environments.
    """
    if settings.is_production:
        raise RuntimeError(
            "drop_and_recreate_db() is disabled in production. "
            "Use Alembic migrations instead."
        )

    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        logger.info("Database dropped and recreated.")


async def check_db_connection() -> bool:
    """Test database connectivity."""
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False
