"""Filesystem paths for persistent browser profiles and scrape scripts."""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.utils.browser_extension import extension_dir


def profiles_root() -> Path:
    path = Path(settings.browser_profiles_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_user_data_dir(profile_id: int) -> Path:
    # Firefox profile layout differs from Chromium; use a separate dir so old
    # Chrome/Chromium user-data does not corrupt the Firefox session.
    path = profiles_root() / f"firefox-profile-{profile_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


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
