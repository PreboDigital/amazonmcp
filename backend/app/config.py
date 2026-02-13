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
    secret_key: str = "change-me-in-production"
    api_key: str = ""  # Required in production; in dev, empty = auth disabled
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    encryption_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    anthropic_api_key: str = ""
    # PA-API (Product Advertising API) for product images
    paapi_access_key: str = ""
    paapi_secret_key: str = ""
    paapi_partner_tag: str = ""

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
        return [o.strip() for o in self.cors_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()
