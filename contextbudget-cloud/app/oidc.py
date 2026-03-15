from __future__ import annotations

"""OIDC / JWT authentication for the ContextBudget Cloud control plane.

Configuration (environment variables)
--------------------------------------
CB_CLOUD_OIDC_ISSUER      OIDC issuer URL, e.g. https://accounts.google.com
                           or https://mycompany.okta.com/oauth2/default
CB_CLOUD_OIDC_AUDIENCE    Expected `aud` claim in the JWT (your app's client ID)
CB_CLOUD_OIDC_JWKS_URI    (optional) Override the JWKS URI; defaults to
                           {issuer}/.well-known/jwks.json
CB_CLOUD_OIDC_ENABLED     Set to "true" to enable OIDC auth (default: false)

When OIDC is disabled the dependency falls through to the existing API-key
authentication path, so this is fully backwards-compatible.

Usage in FastAPI
----------------
    from app.oidc import OIDCToken, optional_oidc_token

    @app.get("/protected")
    async def endpoint(token: OIDCToken | None = Depends(optional_oidc_token)):
        if token:
            return {"sub": token.sub, "email": token.email}
        # fall back to API-key auth ...
"""

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError
from fastapi import Depends, Header, HTTPException

from app.metrics import OIDC_AUTH_FAILURE, OIDC_AUTH_SUCCESS

logger = logging.getLogger(__name__)

OIDC_ENABLED: bool = os.getenv("CB_CLOUD_OIDC_ENABLED", "false").lower() == "true"
_ISSUER: str = os.getenv("CB_CLOUD_OIDC_ISSUER", "")
_AUDIENCE: str = os.getenv("CB_CLOUD_OIDC_AUDIENCE", "")
_JWKS_URI: str = os.getenv("CB_CLOUD_OIDC_JWKS_URI", "")

# Simple in-process JWKS cache — refreshed once per process restart.
_jwks_cache: dict[str, Any] | None = None


async def _fetch_jwks() -> dict[str, Any]:
    global _jwks_cache
    if _jwks_cache is not None:
        return _jwks_cache

    jwks_uri = _JWKS_URI or f"{_ISSUER.rstrip('/')}/.well-known/jwks.json"
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        return _jwks_cache


@dataclass
class OIDCToken:
    """Parsed, verified OIDC ID/Access token claims."""

    sub: str
    email: str | None
    name: str | None
    claims: dict[str, Any]

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> OIDCToken:
        return cls(
            sub=claims["sub"],
            email=claims.get("email"),
            name=claims.get("name"),
            claims=claims,
        )


async def verify_oidc_token(raw_token: str) -> OIDCToken | None:
    """Verify a Bearer JWT against the configured OIDC issuer.

    Returns the parsed token on success, ``None`` on failure (so callers
    can fall back to API-key auth).
    """
    if not OIDC_ENABLED or not _ISSUER or not _AUDIENCE:
        return None

    try:
        jwks_data = await _fetch_jwks()
        key_set = JsonWebKey.import_key_set(jwks_data)
        jwt = JsonWebToken(["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"])
        claims = jwt.decode(raw_token, key_set)
        claims.validate()

        # Validate issuer and audience manually for flexibility
        if claims.get("iss") != _ISSUER:
            OIDC_AUTH_FAILURE.labels(reason="issuer_mismatch").inc()
            return None

        aud = claims.get("aud", "")
        if isinstance(aud, str):
            aud = [aud]
        if _AUDIENCE not in aud:
            OIDC_AUTH_FAILURE.labels(reason="audience_mismatch").inc()
            return None

        OIDC_AUTH_SUCCESS.inc()
        return OIDCToken.from_claims(dict(claims))

    except JoseError as exc:
        OIDC_AUTH_FAILURE.labels(reason="jose_error").inc()
        logger.debug("OIDC JWT verification failed: %s", exc)
        return None
    except Exception as exc:
        OIDC_AUTH_FAILURE.labels(reason="unexpected").inc()
        logger.warning("OIDC verification unexpected error: %s", exc)
        return None


async def optional_oidc_token(
    authorization: str | None = Header(default=None),
) -> OIDCToken | None:
    """FastAPI dependency: extract and verify an OIDC Bearer token if present.

    Returns ``None`` (no exception) when:
    - OIDC is disabled
    - No Authorization header is present
    - Token verification fails (callers should fall back to API-key auth)
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    raw = authorization.removeprefix("Bearer ")
    return await verify_oidc_token(raw)


async def require_oidc_token(
    authorization: str | None = Header(default=None),
) -> OIDCToken:
    """FastAPI dependency: require a valid OIDC token or raise 401."""
    if not OIDC_ENABLED:
        raise HTTPException(status_code=501, detail="OIDC authentication is not enabled")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="OIDC Bearer token required")
    raw = authorization.removeprefix("Bearer ")
    token = await verify_oidc_token(raw)
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid or expired OIDC token")
    return token
