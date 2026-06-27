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
_SKIP_FILE_NAMES = {
    ".build-meta.json",
    ".env",
    ".env.example",
    ".webignore",
    "package.json",
    "package-lock.json",
    "README.md",
}
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


def _file_bytes_for_fingerprint(path: Path) -> bytes:
    if path.name == "manifest.json":
        # Derived from .keys/chrome.pem; excluding avoids false fingerprint drift.
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.pop("key", None)
        return json.dumps(manifest, sort_keys=True).encode("utf-8")
    return path.read_bytes()


def source_fingerprint(ext_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(ext_dir.rglob("*")):
        if not _is_source_file(ext_dir, path):
            continue
        rel = path.relative_to(ext_dir).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(_file_bytes_for_fingerprint(path))
    return digest.hexdigest()


def _read_manifest_version(ext_dir: Path) -> str:
    manifest = json.loads((ext_dir / "manifest.json").read_text(encoding="utf-8"))
    return str(manifest.get("version") or "0.0.0")


def _meta_path(ext_dir: Path) -> Path:
    return ext_dir / ".build-meta.json"


def _legacy_meta_path(ext_dir: Path) -> Path:
    return ext_dir / "dist" / ".build-meta.json"


def _load_meta(ext_dir: Path) -> dict[str, Any] | None:
    for path in (_meta_path(ext_dir), _legacy_meta_path(ext_dir)):
        if not path.is_file():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
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
    (ext_dir / "dist").mkdir(parents=True, exist_ok=True)
    _meta_path(ext_dir).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    legacy = _legacy_meta_path(ext_dir)
    if legacy.is_file():
        legacy.unlink()
    return meta


def _public_meta(ext_dir: Path, meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if not meta:
        return None
    chrome_file = _resolve_chrome_filename(ext_dir, meta)
    firefox_file = _resolve_firefox_filename(ext_dir, meta)
    return {
        "version": meta.get("version"),
        "fingerprint": meta.get("fingerprint"),
        "built_at": meta.get("built_at"),
        "chrome": _artifact_info(ext_dir, chrome_file),
        "firefox": _artifact_info(ext_dir, firefox_file),
    }


def get_status() -> dict[str, Any]:
    ext_dir = extension_dir()
    with _lock:
        status = _state["status"]
        error = _state["error"]
        meta = _state["meta"]

    if ext_dir is not None:
        current, disk_meta = _build_is_current(ext_dir)
        if current:
            meta = disk_meta
            if status in {"idle", "signing"}:
                status = "ready"
                error = None
        elif meta is None:
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


def _chrome_artifact_for_version(ext_dir: Path) -> str | None:
    version = _read_manifest_version(ext_dir)
    filename = f"order-manager-{version}.crx"
    return filename if (ext_dir / "dist" / filename).is_file() else None


def _firefox_artifact_for_version(ext_dir: Path) -> str | None:
    version = _read_manifest_version(ext_dir)
    preferred = f"order_manager_browser_integration-{version}.xpi"
    if (ext_dir / "dist" / preferred).is_file():
        return preferred
    for path in sorted(ext_dir.glob("dist/*.xpi")):
        if f"-{version}.xpi" in path.name or path.name.endswith(f"{version}.xpi"):
            return path.name
    return None


def _artifacts_for_current_version(ext_dir: Path) -> tuple[str | None, str | None]:
    return _chrome_artifact_for_version(ext_dir), _firefox_artifact_for_version(ext_dir)


def _find_chrome_artifact(ext_dir: Path) -> str | None:
    found = _chrome_artifact_for_version(ext_dir)
    if found:
        return found
    matches = sorted(ext_dir.glob("dist/order-manager-*.crx"))
    return matches[-1].name if matches else None


def _find_firefox_artifact(ext_dir: Path) -> str | None:
    found = _firefox_artifact_for_version(ext_dir)
    if found:
        return found
    matches = sorted(ext_dir.glob("dist/*.xpi"))
    return matches[-1].name if matches else None


def _firefox_signing_enabled() -> bool:
    return bool(settings.web_ext_api_key.strip() and settings.web_ext_api_secret.strip())


def _resolve_chrome_filename(ext_dir: Path, meta: dict[str, Any] | None) -> str | None:
    found = _chrome_artifact_for_version(ext_dir)
    if found:
        return found
    filename = (meta or {}).get("chrome_filename")
    if filename and (ext_dir / "dist" / filename).is_file():
        return filename
    return _find_chrome_artifact(ext_dir)


def _resolve_firefox_filename(ext_dir: Path, meta: dict[str, Any] | None) -> str | None:
    found = _firefox_artifact_for_version(ext_dir)
    if found:
        return found
    filename = (meta or {}).get("firefox_filename")
    if filename and (ext_dir / "dist" / filename).is_file():
        return filename
    return _find_firefox_artifact(ext_dir)


def _build_is_current(ext_dir: Path) -> tuple[bool, dict[str, Any] | None]:
    """True when signed artifacts for the current manifest version already exist on disk."""
    meta = _load_meta(ext_dir)
    fingerprint = source_fingerprint(ext_dir)
    chrome_file, firefox_file = _artifacts_for_current_version(ext_dir)
    firefox_needed = _firefox_signing_enabled()

    if not chrome_file:
        return False, meta
    if firefox_needed and not firefox_file:
        return False, meta

    meta = _write_meta(ext_dir, fingerprint, chrome_file, firefox_file)
    return True, meta


def _npm_env() -> dict[str, str]:
    env = os.environ.copy()
    if settings.web_ext_api_key:
        env["WEB_EXT_API_KEY"] = settings.web_ext_api_key
    if settings.web_ext_api_secret:
        env["WEB_EXT_API_SECRET"] = settings.web_ext_api_secret
    return env


def _sign_extension(ext_dir: Path, *, force: bool = False) -> dict[str, Any]:
    fingerprint = source_fingerprint(ext_dir)
    chrome_file, firefox_file = _artifacts_for_current_version(ext_dir)
    firefox_needed = _firefox_signing_enabled()

    chrome_ok = chrome_file is not None
    firefox_ok = firefox_file is not None

    if not force and chrome_ok and (firefox_ok or not firefox_needed):
        logger.info("Browser extension unchanged; using existing signed artifacts")
        return _write_meta(ext_dir, fingerprint, chrome_file, firefox_file)

    _ensure_npm_deps(ext_dir)

    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm is not installed; cannot sign browser extension")

    env = _npm_env()

    if force or not chrome_ok:
        _ensure_chrome_key(ext_dir)
        _run([npm, "run", "sign:chrome"], cwd=ext_dir, env=env)
        chrome_file = _chrome_artifact_for_version(ext_dir) or _find_chrome_artifact(ext_dir)
    else:
        logger.info("Chrome extension unchanged; reusing existing signed artifact")

    if firefox_needed:
        if force or not firefox_ok:
            _run([npm, "run", "sign:firefox"], cwd=ext_dir, env=env)
            firefox_file = _firefox_artifact_for_version(ext_dir) or _find_firefox_artifact(ext_dir)
        else:
            logger.info("Firefox extension unchanged; reusing existing signed artifact")
    else:
        firefox_file = None

    if not chrome_file:
        raise RuntimeError("Chrome signing did not produce an artifact")

    meta = _write_meta(ext_dir, fingerprint, chrome_file, firefox_file)
    logger.info("Browser extension signed (version %s)", meta.get("version"))
    return meta


def ensure_signed(*, force: bool = False) -> None:
    ext_dir = extension_dir()
    if ext_dir is None:
        with _lock:
            _state["status"] = "unavailable"
            _state["error"] = "Browser extension directory not found"
        return

    if not force:
        current, existing = _build_is_current(ext_dir)
        if current and existing:
            with _lock:
                _state["status"] = "ready"
                _state["meta"] = existing
                _state["error"] = None
            logger.info("Browser extension unchanged; using existing signed artifacts")
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
    filename = (
        _resolve_chrome_filename(ext_dir, meta)
        if browser == "chrome"
        else _resolve_firefox_filename(ext_dir, meta)
    )
    if not filename:
        return None
    path = ext_dir / "dist" / filename
    return path if path.is_file() else None
