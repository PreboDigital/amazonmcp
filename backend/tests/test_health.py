"""
Tests for the FastAPI application and health endpoint.
"""

import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_health_endpoint_healthy():
    """Health endpoint should return healthy when DB is connected."""
    with patch("app.main.check_db_connection", new_callable=AsyncMock, return_value=True):
        from app.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert data["database"] == "connected"
            assert data["service"] == "Amazon Ads Optimizer"


@pytest.mark.anyio
async def test_health_endpoint_degraded():
    """Health endpoint should return degraded when DB is disconnected."""
    with patch("app.main.check_db_connection", new_callable=AsyncMock, return_value=False):
        from app.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "degraded"
            assert data["database"] == "disconnected"
