"""
Tests for application configuration and settings validation.
"""

import os
import pytest
from unittest.mock import patch


def test_settings_loads_defaults():
    """Settings should load with sensible defaults in development."""
    # Clear the lru_cache so we get a fresh Settings instance
    from app.config import get_settings
    get_settings.cache_clear()

    with patch.dict(os.environ, {
        "ENVIRONMENT": "development",
        "DATABASE_URL": "postgresql+asyncpg://localhost/test",
        "SECRET_KEY": "change-me-in-production",
    }, clear=False):
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.environment == "development"
        assert settings.is_production is False
        assert settings.openai_model == "gpt-4o"
        get_settings.cache_clear()


def test_settings_cors_origin_list():
    """CORS origins string should be split into a list."""
    from app.config import get_settings
    get_settings.cache_clear()

    with patch.dict(os.environ, {
        "ENVIRONMENT": "development",
        "DATABASE_URL": "postgresql+asyncpg://localhost/test",
        "CORS_ORIGINS": "http://localhost:3000, http://example.com",
    }, clear=False):
        get_settings.cache_clear()
        settings = get_settings()
        origins = settings.cors_origin_list
        assert len(origins) == 2
        assert "http://localhost:3000" in origins
        assert "http://example.com" in origins
        get_settings.cache_clear()


def test_production_rejects_default_secret():
    """Production mode should reject the default secret key."""
    from app.config import get_settings, Settings
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="SECRET_KEY must be set"):
        Settings(
            environment="production",
            secret_key="change-me-in-production",
            database_url="postgresql+asyncpg://prod-host/db",
        )
    get_settings.cache_clear()


def test_production_accepts_real_secret():
    """Production mode should accept a real secret key."""
    from app.config import Settings
    settings = Settings(
        environment="production",
        secret_key="a-real-secret-key-that-is-not-the-default",
        database_url="postgresql+asyncpg://prod-host/db",
    )
    assert settings.is_production is True
    assert settings.secret_key == "a-real-secret-key-that-is-not-the-default"
