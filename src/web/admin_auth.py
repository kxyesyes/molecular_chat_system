from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException, status


ADMIN_TOKEN_ENV = "MEDCHAT_ADMIN_TOKEN"
ADMIN_TOKEN_HEADER = "X-MedChat-Admin-Token"


def _configured_admin_token() -> str:
    return os.getenv(ADMIN_TOKEN_ENV, "").strip()


def _bearer_token(authorization: str | None) -> str:
    value = (authorization or "").strip()
    if not value:
        return ""
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


async def require_admin(
    x_medchat_admin_token: str | None = Header(default=None, alias=ADMIN_TOKEN_HEADER),
    authorization: str | None = Header(default=None),
) -> bool:
    """Require the local admin token for operational/management APIs."""

    expected = _configured_admin_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{ADMIN_TOKEN_ENV} is not configured",
        )

    supplied = (x_medchat_admin_token or "").strip() or _bearer_token(authorization)
    if not supplied:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin token",
        )

    return True
