"""Browser extension API schemas."""
from pydantic import BaseModel


class BrowserExtensionArtifact(BaseModel):
    filename: str
    size_bytes: int
    updated_at: str


class BrowserExtensionMeta(BaseModel):
    version: str
    fingerprint: str
    built_at: str
    chrome: BrowserExtensionArtifact | None = None
    firefox: BrowserExtensionArtifact | None = None


class BrowserExtensionStatus(BaseModel):
    status: str
    error: str | None = None
    available: bool
    build_enabled: bool = False
    meta: BrowserExtensionMeta | None = None
