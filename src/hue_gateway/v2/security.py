from __future__ import annotations

import secrets

from fastapi import Request

from hue_gateway.security import AuthContext


def _is_allowed(value: str, allowed: list[str]) -> bool:
    for item in allowed:
        if secrets.compare_digest(value, item):
            return True
    return False


def authenticate_v2(request: Request) -> AuthContext | None:
    state = request.app.state.state
    config = state.config

    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        token = authz[len("bearer ") :].strip()
        if token and _is_allowed(token, config.auth_tokens):
            return AuthContext(credential=token, scheme="bearer")

    api_key = request.headers.get("x-api-key")
    if api_key:
        api_key = api_key.strip()
        if api_key and _is_allowed(api_key, config.api_keys):
            return AuthContext(credential=api_key, scheme="api_key")

    return None

