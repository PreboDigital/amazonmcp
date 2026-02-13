import logging
from pydantic_settings import BaseSettings
from pydantic import model_validator, ConfigDict
from functools import lru_cache

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment: "development" or "production"
    environment: str = "development"

    database_url: str = "postgresql+asyncpg://localhost/amazon_ads"

    @model_validator(mode="before")
    @classmethod
    def _fix_database_url_for_asyncpg(cls, values: dict) -> dict:
        """Railway/Postgres gives postgresql:// — we need postgresql+asyncpg:// for asyncpg."""
        if not isinstance(values, dict):
            return values
        url = values.get("database_url") or ""
        if url.startswith("postgresql://") and "+asyncpg" not in url:
            values["database_url"] = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return values
    secret_key: str = "change-me-in-production"
    api_key: str = ""  # Required in production; in dev, empty = auth disabled
    first_admin_email: str = ""  # Bootstrap: create first admin if no users exist
    first_admin_password: str = ""
    cors_origins: str = "http://localhost:5173,http://localhost:3000,https://amazonmcp-frontend-production.up.railway.app"
    encryption_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    anthropic_api_key: str = ""
    # PA-API (Product Advertising API) for product images
    paapi_access_key: str = ""
    paapi_secret_key: str = ""
    paapi_partner_tag: str = ""

    # Resend (email notifications)
    resend_api_key: str = ""
    from_email: str = ""

    # Upstash Redis (caching, future job queues)
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""

    @model_validator(mode="after")
    def _validate_production_settings(self) -> "Settings":
        """Enforce that critical secrets are set when running in production."""
        if self.is_production:
            if self.secret_key == "change-me-in-production":
                raise ValueError(
                    "SECRET_KEY must be set to a secure value in production. "
                    "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if not self.api_key:
                raise ValueError(
                    "API_KEY must be set in production. "
                    "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if not self.encryption_key:
                raise ValueError(
                    "ENCRYPTION_KEY must be set in production. "
                    "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )
            if not self.database_url or "localhost" in self.database_url:
                logger.warning("DATABASE_URL appears to point at localhost in production.")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        # Always include production frontend when in production (credentials require explicit origin)
        prod_frontend = "https://amazonmcp-frontend-production.up.railway.app"
        if self.is_production and prod_frontend not in origins:
            origins.append(prod_frontend)
        return origins or [prod_frontend, "http://localhost:5173", "http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
