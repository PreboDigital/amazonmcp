"""
Amazon Ads Optimizer — FastAPI Backend
Connects to the official Amazon Ads MCP Server for campaign optimization.
All data persisted to PostgreSQL.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.database import init_db, check_db_connection
from app.auth import require_auth
from app.routers import credentials, audit, harvest, optimizer, accounts, ai, approvals, reporting, campaigns, settings as settings_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Amazon Ads Optimizer...")
    await init_db()
    logger.info("Database initialized — all tables ready.")
    yield
    logger.info("Shutting down...")


app = FastAPI(
    title="Amazon Ads Optimizer",
    description="Campaign optimization powered by the Amazon Ads MCP Server",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register Routers (all require auth) ──────────────────────────────
_auth = [Depends(require_auth)]
app.include_router(credentials.router, prefix="/api/credentials", tags=["Credentials"], dependencies=_auth)
app.include_router(accounts.router, prefix="/api/accounts", tags=["Accounts"], dependencies=_auth)
app.include_router(audit.router, prefix="/api/audit", tags=["Audit & Reports"], dependencies=_auth)
app.include_router(harvest.router, prefix="/api/harvest", tags=["Keyword Harvesting"], dependencies=_auth)
app.include_router(optimizer.router, prefix="/api/optimizer", tags=["Bid Optimizer"], dependencies=_auth)
app.include_router(ai.router, prefix="/api/ai", tags=["AI Assistant"], dependencies=_auth)
app.include_router(approvals.router, prefix="/api/approvals", tags=["Approval Queue"], dependencies=_auth)
app.include_router(reporting.router, prefix="/api/reports", tags=["Reports"], dependencies=_auth)
app.include_router(campaigns.router, prefix="/api/campaigns", tags=["Campaign Management"], dependencies=_auth)
app.include_router(settings_router.router, prefix="/api/settings", tags=["Settings"], dependencies=_auth)


@app.get("/api/health")
async def health_check():
    db_ok = await check_db_connection()
    return {
        "status": "healthy" if db_ok else "degraded",
        "service": "Amazon Ads Optimizer",
        "database": "connected" if db_ok else "disconnected",
    }
