"""
Alembic environment. Loads DATABASE_URL from app config.
Uses asyncpg (same as app) for migrations.
"""

import asyncio
import os
import ssl
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from alembic import context

# Load app config for DATABASE_URL
os.environ.setdefault("ENVIRONMENT", "development")
from app.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use app's DATABASE_URL (postgresql+asyncpg://)
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# Import models for autogenerate
from app.database import Base
import app.models  # noqa: F401

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def _get_connect_args():
    """SSL for Railway Postgres (rlwy.net)."""
    url = config.get_main_option("sqlalchemy.url", "")
    args = {"timeout": 30}
    if "rlwy.net" in url:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        args["ssl"] = ctx
    return args


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = create_async_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=NullPool,
        connect_args=_get_connect_args(),
    )

    async def run_async():
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)

    asyncio.run(run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
