"""Filesystem paths for persistent browser profiles and scrape scripts."""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.utils.browser_extension import extension_dir


def profiles_root() -> Path:
    path = Path(settings.browser_profiles_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def browser_engine_for_retailer(retailer: str) -> str:
    """Costco Azure B2C and Amazon automations use Chromium; Walmart stays on Camoufox."""
    if (retailer or "").strip().lower() in {"amazon", "costco"}:
        return "chromium"
    return "camoufox"


def profile_user_data_dir(profile_id: int, *, browser: str = "camoufox") -> Path:
    # Keep engine-specific dirs so switching retailers → Chromium does not reuse a Firefox profile.
    prefix = "chromium-profile" if browser == "chromium" else "camoufox-profile"
    path = profiles_root() / f"{prefix}-{profile_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def all_profile_data_dirs(profile_id: int) -> list[Path]:
    """All on-disk profile dirs for a profile id (current + legacy engines)."""
    root = profiles_root()
    return [
        root / f"camoufox-profile-{profile_id}",
        root / f"chromium-profile-{profile_id}",
        root / f"profile-{profile_id}",
        root / f"firefox-profile-{profile_id}",
    ]


def _extension_root() -> Path:
    ext = extension_dir()
    if ext is None:
        raise FileNotFoundError("Browser extension directory not found; cannot load scrape scripts.")
    return ext


def amazon_script_paths() -> list[Path]:
    """Ordered scripts to inject into Amazon pages (extension sources)."""
    ext = _extension_root()
    files = [
        ext / "lib" / "amazon.js",
        ext / "stores" / "amazon" / "selectors.js",
        ext / "stores" / "amazon" / "dom.js",
    ]
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing Amazon scrape scripts: {', '.join(missing)}")
    return files


def walmart_orders_script_path() -> Path:
    path = _extension_root() / "stores" / "walmart" / "orders.js"
    if not path.is_file():
        raise FileNotFoundError(f"Missing Walmart orders hook script: {path}")
    return path


def walmart_script_paths() -> list[Path]:
    """Scripts to inject for Walmart capture + normalize."""
    files = [
        _extension_root() / "lib" / "walmart.js",
        walmart_orders_script_path(),
    ]
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing Walmart scrape scripts: {', '.join(missing)}")
    return files


def costco_script_paths() -> list[Path]:
    """Scripts to inject for Costco normalize (GraphQL capture is via Playwright responses)."""
    files = [
        _extension_root() / "lib" / "costco.js",
    ]
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing Costco scrape scripts: {', '.join(missing)}")
    return files
