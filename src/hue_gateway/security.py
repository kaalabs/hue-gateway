from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer


@dataclass(frozen=True)
class AuthContext:
    credential: str
    scheme: str  # "bearer" | "api_key"


def _is_allowed(value: str, allowed: list[str]) -> bool:
    for item in allowed:
        if secrets.compare_digest(value, item):
            return True
    return False


_bearer = HTTPBearer(auto_error=False)
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_auth(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer),
    api_key: str | None = Depends(_api_key),
) -> AuthContext:
    state = request.app.state.state
    config = state.config

    if bearer and bearer.scheme.lower() == "bearer":
        token = bearer.credentials.strip()
        if token and _is_allowed(token, config.auth_tokens):
            return AuthContext(credential=token, scheme="bearer")

    if api_key and _is_allowed(api_key, config.api_keys):
        return AuthContext(credential=api_key, scheme="api_key")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": "unauthorized"},
    )
