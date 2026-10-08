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

    # Directory where imported store invoice PDFs are stored
    invoice_dir: str = "data/invoices"

    # Persistent Camoufox profiles for automated store import (cookies/MFA)
    browser_profiles_dir: str = "data/browser_profiles"
    # Cap concurrent browsers (login + import)
    browser_max_concurrent: int = 2
    # Headless=True is heavily flagged by Walmart/Amazon bot checks. Prefer headed
    # Camoufox under Xvfb in Docker. Invoice PDF rendering stays separately headless Chromium.
    browser_headless: bool = False
    # Optional UA override; leave empty so Camoufox owns the fingerprint.
    browser_user_agent: str = ""
    # Public app base URL used when building import-review links (no trailing slash)
    app_public_base_url: str = "http://localhost:5173"

    # Browser extension signing (optional; auto-detects repo browser-extension/ if unset)
    browser_extension_dir: str = ""
    web_ext_api_key: str = ""
    web_ext_api_secret: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
