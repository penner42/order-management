"""Application configuration."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Settings loaded from environment."""

    app_name: str = "Order Management System"
    debug: bool = False

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/order_management"

    # Auth
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days

    # Admin user (from env; created/updated on startup)
    admin_username: str = "admin"
    admin_password: str = "admin"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
