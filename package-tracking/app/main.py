import base64
import os
import threading
import time

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# UPS OAuth: production https://onlinetools.ups.com/security/v1/oauth/token
# Sandbox: https://wwwcie.ups.com/security/v1/oauth/token
UPS_OAUTH_URL = os.environ.get(
    "UPS_OAUTH_URL", "https://onlinetools.ups.com/security/v1/oauth/token"
)

# Cache token until this many seconds before expiry (avoid using an expired token)
_UPS_TOKEN_BUFFER_SEC = 60
_ups_token_cache: tuple[str, float] | None = None
_ups_token_lock = threading.Lock()


class LinkRequest(BaseModel):
    tracking_number: str


class LinkResponse(BaseModel):
    carrier: str | None
    url: str | None


app = FastAPI(title="Package Tracking API")


def _normalize(tracking: str) -> str:
    return (tracking or "").strip().replace(" ", "")


def _is_ups(t: str) -> bool:
    if len(t) < 9:
        return False
    import re

    if re.fullmatch(r"1Z[0-9A-Z]{16}", t, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"[KJ][0-9]{10}", t, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"T\d{10}", t):
        return True
    return False


def _is_usps(t: str) -> bool:
    import re

    if re.fullmatch(r"9[0-4]\d{18,21}", t):
        return True
    if re.fullmatch(r"91\d+", t) and 20 <= len(t) <= 22:
        return True
    if re.fullmatch(r"[0-9]{20,22}", t) and not t.startswith("96"):
        return True
    if re.fullmatch(r"E[A-Z]\d{9}[A-Z]{2}", t, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"[A-Z]{2}\d{9}[A-Z]{2}", t, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"82\d{8}", t):
        return True
    if re.fullmatch(r"(03|23|14|70)\d{16,18}", t):
        return True
    return False


def _is_fedex(t: str) -> bool:
    import re

    if re.fullmatch(r"[0-9]{12}", t):
        return True
    if re.fullmatch(r"[0-9]{15}", t):
        return True
    if re.fullmatch(r"96\d{20}", t):
        return True
    if re.fullmatch(r"96\d{22}", t):
        return True
    if re.fullmatch(r"100\d{31}", t):
        return True
    if re.fullmatch(r"[0-9]{18}", t):
        return True
    return False


def _is_amazon(t: str) -> bool:
    import re

    if re.fullmatch(r"TBA\d{10,}", t, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"AMZN\d+[A-Z]?", t, flags=re.IGNORECASE):
        return True
    return False


def _is_ontrac(t: str) -> bool:
    import re

    if re.fullmatch(r"C\d{14}", t):
        return True
    if re.fullmatch(r"D\d{11,14}", t):
        return True
    if re.fullmatch(r"\d{13,14}", t):
        return True
    return False


def _is_lasership(t: str) -> bool:
    import re

    if re.fullmatch(r"1LS\d{8,}", t, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"LS\d{8,}", t, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"LX\d{8,}", t, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"BN\d{8,}", t, flags=re.IGNORECASE):
        return True
    if (
        re.fullmatch(r"[A-Z]{2}\d{8,12}", t, flags=re.IGNORECASE)
        and 10 <= len(t) <= 15
    ):
        return True
    return False


def _tracking_url(carrier: str, tn: str) -> str:
    from urllib.parse import quote_plus

    encoded = quote_plus(tn)
    if carrier == "UPS":
        return f"https://www.ups.com/track?tracknum={encoded}"
    if carrier == "USPS":
        return f"https://tools.usps.com/go/TrackConfirmAction?tLabels={encoded}"
    if carrier == "FedEx":
        return f"https://www.fedex.com/fedextrack/?trknbr={encoded}"
    if carrier == "Amazon":
        return f"https://www.amazon.com/progress/tracker/{encoded}"
    if carrier == "OnTrac":
        return f"https://www.ontrac.com/tracking?tracking_number={encoded}"
    if carrier == "LaserShip":
        return f"https://www.lasership.com/track/{encoded}"
    raise ValueError(f"Unsupported carrier: {carrier}")


def get_ups_token() -> str:
    """
    Obtain a UPS OAuth 2.0 access token using client credentials (UPS_CLIENT, UPS_SECRET).
    Token is cached and reused until near expiry. Use the returned token as Bearer for UPS API calls.
    """
    global _ups_token_cache
    now = time.monotonic()
    with _ups_token_lock:
        if _ups_token_cache is not None:
            token, expires_at = _ups_token_cache
            if expires_at > now + _UPS_TOKEN_BUFFER_SEC:
                return token

    client_id = os.environ.get("UPS_CLIENT", "").strip()
    client_secret = os.environ.get("UPS_SECRET", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail="UPS credentials not configured (UPS_CLIENT, UPS_SECRET)",
        )
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
        "x-merchant-id": client_id,
    }
    data = "grant_type=client_credentials"
    with httpx.Client() as client:
        resp = client.post(UPS_OAUTH_URL, headers=headers, content=data, timeout=15.0)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"UPS OAuth failed: {resp.status_code} {resp.text}",
        )
    body = resp.json()
    access_token = body.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=502,
            detail="UPS OAuth response missing access_token",
        )
    expires_in = int(body.get("expires_in", 3600))
    expires_at = now + expires_in
    with _ups_token_lock:
        _ups_token_cache = (access_token, expires_at)
    return access_token


def detect_carrier_and_link(tracking_number: str) -> LinkResponse:
    t = _normalize(tracking_number)
    if not t:
        return LinkResponse(carrier=None, url=None)

    if _is_ups(t):
        carrier = "UPS"
    elif _is_amazon(t):
        carrier = "Amazon"
    elif _is_lasership(t):
        carrier = "LaserShip"
    elif _is_ontrac(t):
        carrier = "OnTrac"
    elif _is_usps(t):
        carrier = "USPS"
    elif _is_fedex(t):
        carrier = "FedEx"
    else:
        return LinkResponse(carrier=None, url=None)

    return LinkResponse(carrier=carrier, url=_tracking_url(carrier, t))


@app.post("/link", response_model=LinkResponse)
def link(body: LinkRequest) -> LinkResponse:
    return detect_carrier_and_link(body.tracking_number)


@app.get("/ups-token")
def ups_token() -> dict:
    """Return a UPS OAuth access token for use in subsequent UPS API requests (e.g. tracking)."""
    token = get_ups_token()
    return {"access_token": token, "token_type": "Bearer"}


@app.get("/track")
def track_stub() -> dict:
    # Stub endpoint for future detailed tracking integration
    raise HTTPException(status_code=501, detail="Tracking endpoint not implemented yet")


@app.get("/")
def root() -> dict:
    return {"message": "Package Tracking API", "docs": "/docs"}

