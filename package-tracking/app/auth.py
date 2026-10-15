"""Validate tracking requests against the order manager's active users."""
import os

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer(auto_error=False)


async def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    if credentials is None:
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    backend_url = os.environ.get("ORDER_MANAGEMENT_API_BASE", "http://backend:8000").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{backend_url}/api/auth/me",
                headers={"Authorization": f"Bearer {credentials.credentials}"},
            )
    except httpx.RequestError:
        raise HTTPException(503, "Authentication service unavailable") from None
    if response.status_code in (401, 403):
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    if response.status_code != 200:
        raise HTTPException(503, "Authentication service unavailable")
    return response.json()
