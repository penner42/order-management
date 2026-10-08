"""Filesystem paths for persistent browser profiles and Amazon scrape scripts."""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.utils.browser_extension import extension_dir


def profiles_root() -> Path:
    path = Path(settings.browser_profiles_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_user_data_dir(profile_id: int) -> Path:
    path = profiles_root() / f"profile-{profile_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def amazon_script_paths() -> list[Path]:
    """Ordered scripts to inject into Amazon pages (extension sources)."""
    ext = extension_dir()
    if ext is None:
        raise FileNotFoundError("Browser extension directory not found; cannot load Amazon scrape scripts.")
    files = [
        ext / "lib" / "amazon.js",
        ext / "stores" / "amazon" / "selectors.js",
        ext / "stores" / "amazon" / "dom.js",
    ]
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing Amazon scrape scripts: {', '.join(missing)}")
    return files
