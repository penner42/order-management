"""Browser extension download API."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.auth import get_current_user
from app.models import User
from app.schemas.browser_extension import BrowserExtensionStatus
from app.utils import browser_extension as ext

router = APIRouter(prefix="/browser-extension", tags=["browser-extension"])


@router.get("", response_model=BrowserExtensionStatus)
def get_extension_status(_: User = Depends(get_current_user)):
    return ext.get_status()


@router.post("/rebuild", response_model=BrowserExtensionStatus)
def rebuild_extension(_: User = Depends(get_current_user)):
    status = ext.get_status()
    if not status["available"]:
        raise HTTPException(status_code=503, detail="Browser extension directory not available")
    if status["status"] == "signing":
        return status
    ext.ensure_signed_async(force=True)
    return ext.get_status()


@router.get("/download/{browser}")
def download_extension(browser: str, _: User = Depends(get_current_user)):
    if browser not in {"chrome", "firefox"}:
        raise HTTPException(status_code=404, detail="Unknown browser target")
    path = ext.artifact_path(browser)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=f"No signed {browser} extension available yet",
        )
    media = "application/x-chrome-extension" if browser == "chrome" else "application/x-xpinstall"
    return FileResponse(path, media_type=media, filename=path.name)
