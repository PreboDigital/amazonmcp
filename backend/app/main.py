"""
Amazon Ads Optimizer — FastAPI Backend
Connects to the official Amazon Ads MCP Server for campaign optimization.
All data persisted to PostgreSQL.
Serves frontend static files when present (unified deploy = no CORS).
"""

import logging
from pathlib import Path
from contextlib import asynccontextmanager
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, FileResponse
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.database import init_db, check_db_connection
from app.auth import require_auth
from app.routers import (
    credentials, audit, harvest, optimizer, accounts, ai, approvals,
    reporting, campaigns, settings as settings_router, cron, auth, users,
)
from app.models import User
from app.services.auth_service import hash_password
from sqlalchemy import select, func

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


async def _bootstrap_first_admin():
    """Create first admin if FIRST_ADMIN_EMAIL and FIRST_ADMIN_PASSWORD are set and no users exist."""
    if not settings.first_admin_email or not settings.first_admin_password:
        return
    from app.database import async_session
    async with async_session() as db:
        r = await db.execute(select(func.count()).select_from(User))
        count = r.scalar() or 0
        if count > 0:
            return  # Users already exist
        admin = User(
            email=settings.first_admin_email.lower(),
            password_hash=hash_password(settings.first_admin_password),
            name="Admin",
            role="admin",
            is_active=True,
        )
        db.add(admin)
        await db.commit()
        logger.info(f"Bootstrap: created first admin user {admin.email}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Amazon Ads Optimizer...")
    try:
        await init_db()
        await _bootstrap_first_admin()
        logger.info("Database initialized — all tables ready.")
    except Exception as e:
        logger.error(f"Startup failed (DB/init): {e}", exc_info=True)
        # Still yield so app can serve /api/health (degraded) and logs are visible
    yield
    logger.info("Shutting down...")


app = FastAPI(
    title="Amazon Ads Optimizer",
    description="Campaign optimization powered by the Amazon Ads MCP Server",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS: allow production frontend + localhost
CORS_ORIGINS = [
    "https://amazonmcp-frontend-production.up.railway.app",
    "http://localhost:5173",
    "http://localhost:3000",
]


class AddCORSHeadersMiddleware(BaseHTTPMiddleware):
    """Ensure CORS headers on ALL responses (including errors). Runs before CORSMiddleware."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS":
            origin = request.headers.get("origin", "")
            if origin in CORS_ORIGINS or not origin:
                return Response(
                    status_code=200,
                    headers={
                        "Access-Control-Allow-Origin": origin or CORS_ORIGINS[0],
                        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                        "Access-Control-Allow-Headers": "Content-Type, Authorization",
                        "Access-Control-Allow-Credentials": "true",
                        "Access-Control-Max-Age": "86400",
                    },
                )
        response = await call_next(request)
        origin = request.headers.get("origin", "")
        if origin in CORS_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        return response


# Add CORSMiddleware first (runs second); AddCORSHeadersMiddleware last (runs first for OPTIONS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AddCORSHeadersMiddleware)

# ── Auth (login/register public; whoami requires JWT) ─────────────────
app.include_router(auth.router, prefix="/api")

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
app.include_router(users.router, prefix="/api", dependencies=[Depends(require_auth)])
app.include_router(cron.router, prefix="/api")  # No auth — uses CRON_SECRET


@app.get("/api/health")
async def health_check():
    db_ok = await check_db_connection()
    return {
        "status": "healthy" if db_ok else "degraded",
        "service": "Amazon Ads Optimizer",
        "database": "connected" if db_ok else "disconnected",
    }


# Static files + SPA fallback (when backend/static exists = unified deploy, no CORS)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        """Serve SPA for non-API routes. API routes registered above."""
        if full_path.startswith("api") or full_path == "api":
            return Response(status_code=404)
        file_path = STATIC_DIR / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(STATIC_DIR / "index.html")
