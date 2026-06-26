"""Build and sign the browser extension when source files change."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_SKIP_DIR_NAMES = {".git", "node_modules", "dist", ".keys"}
_SKIP_FILE_NAMES = {".env", ".env.example", ".webignore", "package.json", "package-lock.json", "README.md"}
_SKIP_FILE_GLOBS = ("*.crx", "*.xpi")

_lock = threading.Lock()
_state: dict[str, Any] = {
    "status": "idle",
    "error": None,
    "meta": None,
}


def _repo_browser_extension_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "browser-extension"


def extension_dir() -> Path | None:
    configured = settings.browser_extension_dir.strip()
    path = Path(configured) if configured else _repo_browser_extension_dir()
    if not path.is_dir():
        logger.warning("Browser extension directory not found: %s", path)
        return None
    return path


def _is_source_file(ext_dir: Path, path: Path) -> bool:
    if not path.is_file():
        return False
    rel = path.relative_to(ext_dir)
    if any(part in _SKIP_DIR_NAMES for part in rel.parts):
        return False
    if rel.name in _SKIP_FILE_NAMES:
        return False
    if any(fnmatch(rel.name, pattern) for pattern in _SKIP_FILE_GLOBS):
        return False
    if rel.parts and rel.parts[0] == "scripts":
        return False
    return True


def source_fingerprint(ext_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(ext_dir.rglob("*")):
        if not _is_source_file(ext_dir, path):
            continue
        rel = path.relative_to(ext_dir).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _read_manifest_version(ext_dir: Path) -> str:
    manifest = json.loads((ext_dir / "manifest.json").read_text(encoding="utf-8"))
    return str(manifest.get("version") or "0.0.0")


def _meta_path(ext_dir: Path) -> Path:
    return ext_dir / "dist" / ".build-meta.json"


def _load_meta(ext_dir: Path) -> dict[str, Any] | None:
    path = _meta_path(ext_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _artifact_info(ext_dir: Path, filename: str | None) -> dict[str, Any] | None:
    if not filename:
        return None
    path = ext_dir / "dist" / filename
    if not path.is_file():
        return None
    stat = path.stat()
    return {
        "filename": filename,
        "size_bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def _write_meta(ext_dir: Path, fingerprint: str, chrome_file: str | None, firefox_file: str | None) -> dict[str, Any]:
    meta = {
        "version": _read_manifest_version(ext_dir),
        "fingerprint": fingerprint,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "chrome_filename": chrome_file,
        "firefox_filename": firefox_file,
    }
    dist = ext_dir / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    _meta_path(ext_dir).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def _public_meta(ext_dir: Path, meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if not meta:
        return None
    return {
        "version": meta.get("version"),
        "fingerprint": meta.get("fingerprint"),
        "built_at": meta.get("built_at"),
        "chrome": _artifact_info(ext_dir, meta.get("chrome_filename")),
        "firefox": _artifact_info(ext_dir, meta.get("firefox_filename")),
    }


def get_status() -> dict[str, Any]:
    ext_dir = extension_dir()
    with _lock:
        status = _state["status"]
        error = _state["error"]
        meta = _state["meta"]
    if meta is None and ext_dir is not None:
        meta = _load_meta(ext_dir)
    return {
        "status": status,
        "error": error,
        "available": ext_dir is not None,
        "meta": _public_meta(ext_dir, meta) if ext_dir and meta else None,
    }


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    logger.info("Running %s", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def _ensure_npm_deps(ext_dir: Path) -> None:
    node_modules = ext_dir / "node_modules"
    lockfile = ext_dir / "package-lock.json"
    if not node_modules.is_dir() or (
        lockfile.is_file() and lockfile.stat().st_mtime > node_modules.stat().st_mtime
    ):
        npm = shutil.which("npm")
        if not npm:
            raise RuntimeError("npm is not installed; cannot build browser extension")
        _run([npm, "ci"], cwd=ext_dir) if lockfile.is_file() else _run([npm, "install"], cwd=ext_dir)


def _ensure_chrome_key(ext_dir: Path) -> None:
    key_path = ext_dir / ".keys" / "chrome.pem"
    manifest_path = ext_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if key_path.is_file() and manifest.get("key"):
        return
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm is not installed; cannot generate Chrome signing key")
    _run([npm, "run", "generate-key"], cwd=ext_dir)


def _find_chrome_artifact(ext_dir: Path) -> str | None:
    version = _read_manifest_version(ext_dir)
    preferred = ext_dir / "dist" / f"order-manager-{version}.crx"
    if preferred.is_file():
        return preferred.name
    matches = sorted(ext_dir.glob("dist/order-manager-*.crx"))
    return matches[-1].name if matches else None


def _find_firefox_artifact(ext_dir: Path) -> str | None:
    matches = sorted(ext_dir.glob("dist/*.xpi"))
    return matches[-1].name if matches else None


def _sign_extension(ext_dir: Path, *, force: bool = False) -> dict[str, Any]:
    fingerprint = source_fingerprint(ext_dir)
    existing = _load_meta(ext_dir)
    if not force and existing:
        if existing.get("fingerprint") == fingerprint:
            chrome = _artifact_info(ext_dir, existing.get("chrome_filename"))
            firefox = _artifact_info(ext_dir, existing.get("firefox_filename"))
            if chrome and (firefox or not settings.web_ext_api_key or not settings.web_ext_api_secret):
                logger.info("Browser extension unchanged; using existing signed artifacts")
                return existing

    _ensure_npm_deps(ext_dir)
    _ensure_chrome_key(ext_dir)

    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm is not installed; cannot sign browser extension")

    env = os.environ.copy()
    if settings.web_ext_api_key:
        env["WEB_EXT_API_KEY"] = settings.web_ext_api_key
    if settings.web_ext_api_secret:
        env["WEB_EXT_API_SECRET"] = settings.web_ext_api_secret

    _run([npm, "run", "sign:chrome"], cwd=ext_dir, env=env)

    if settings.web_ext_api_key and settings.web_ext_api_secret:
        _run([npm, "run", "sign:firefox"], cwd=ext_dir, env=env)
    else:
        logger.warning(
            "Skipping Firefox signing: set WEB_EXT_API_KEY and WEB_EXT_API_SECRET to enable"
        )

    meta = _write_meta(
        ext_dir,
        fingerprint,
        _find_chrome_artifact(ext_dir),
        _find_firefox_artifact(ext_dir) if settings.web_ext_api_key and settings.web_ext_api_secret else None,
    )
    logger.info("Browser extension signed (version %s)", meta.get("version"))
    return meta


def ensure_signed(*, force: bool = False) -> None:
    ext_dir = extension_dir()
    if ext_dir is None:
        with _lock:
            _state["status"] = "unavailable"
            _state["error"] = "Browser extension directory not found"
        return

    with _lock:
        if _state["status"] == "signing":
            return
        _state["status"] = "signing"
        _state["error"] = None

    try:
        meta = _sign_extension(ext_dir, force=force)
        public = _public_meta(ext_dir, meta)
        with _lock:
            _state["status"] = "ready"
            _state["meta"] = meta
            _state["error"] = None
        logger.info("Browser extension ready: %s", public)
    except Exception as exc:
        logger.exception("Browser extension signing failed")
        with _lock:
            _state["status"] = "error"
            _state["error"] = str(exc)


def ensure_signed_async(*, force: bool = False) -> None:
    thread = threading.Thread(
        target=ensure_signed,
        kwargs={"force": force},
        name="browser-extension-sign",
        daemon=True,
    )
    thread.start()


def artifact_path(browser: str) -> Path | None:
    ext_dir = extension_dir()
    if ext_dir is None:
        return None
    meta = _load_meta(ext_dir) or _state.get("meta")
    if not meta:
        return None
    key = "chrome_filename" if browser == "chrome" else "firefox_filename"
    filename = meta.get(key)
    if not filename:
        return None
    path = ext_dir / "dist" / filename
    return path if path.is_file() else None
