"""
Auth (Clerk): identity comes from a Clerk-issued JWT in the Authorization header,
verified against Clerk's public keys (JWKS) — no shared secret, no static token map.

  Authorization: Bearer <clerk jwt>
    valid signature + issuer + not expired -> claims
    the `sub` claim IS the user_id (everything downstream is keyed by it)
    a custom `role` claim == "admin" unlocks the /admin/* routes

  missing/!invalid token -> 401
  admin route, non-admin   -> 403

The path {user_id} is NOT identity — a user always acts as their token's `sub`,
so they can never drive another user's turn by editing the URL. Admin routes take
the target user_id from the path *after* require_admin has confirmed the caller.
"""

import logging

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from backend import config

log = logging.getLogger("auth")

# One JWKS client, lazily built and cached; it fetches + caches Clerk's signing keys
# and refreshes them when a new key id shows up (Clerk rotates keys).
_jwks_client: PyJWKClient | None = None


def _jwks() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        if not config.CLERK_JWT_ISSUER:
            raise HTTPException(status_code=500, detail="CLERK_JWT_ISSUER not configured")
        _jwks_client = PyJWKClient(f"{config.CLERK_JWT_ISSUER}/.well-known/jwks.json")
    return _jwks_client


def _verify(authorization: str) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        signing_key = _jwks().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=config.CLERK_JWT_ISSUER,
            options={"verify_aud": False},   # Clerk uses azp, not aud; we don't gate on it
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except Exception as e:
        log.warning("jwt verify failed: %r", e)
        raise HTTPException(status_code=401, detail="invalid token")
    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="token has no subject")
    return claims


def require_user(authorization: str = Header(default="")) -> str:
    """Any authenticated Clerk user. Returns the user_id (the JWT `sub`)."""
    return _verify(authorization)["sub"]


def require_admin(authorization: str = Header(default="")) -> str:
    """A Clerk user whose custom `role` claim is "admin". Returns their user_id."""
    claims = _verify(authorization)
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return claims["sub"]
