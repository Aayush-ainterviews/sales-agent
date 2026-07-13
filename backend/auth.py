"""
Auth (Phase 3): identity comes from the bearer token, not the URL path.

`Authorization: Bearer <token>` -> user_id via config.user_tokens().
  unknown token           -> 401
  token's user != path id -> 403  (you can only act as yourself)

For internal users this is a static token map (config.USER_TOKENS). Real
SSO/JWT is a frontend-phase concern.
"""

from fastapi import Header, HTTPException, Path

from backend import config


def require_user(
    user_id: str = Path(...),
    authorization: str = Header(default=""),
) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    tokens = config.user_tokens()
    who = tokens.get(token)
    if who is None:
        raise HTTPException(status_code=401, detail="unknown or missing token")
    if who != user_id:
        raise HTTPException(status_code=403, detail="token does not match path user")
    return who
