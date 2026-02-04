from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Retryable = Literal[True, False, "maybe"]


@dataclass(frozen=True)
class ErrorRegistryEntry:
    code: str
    http_status: int
    retryable: Retryable


# Seed registry aligned to `openapi-v2.skeleton.yaml` and the architecture doc 0v91.
V2_ERROR_CODE_REGISTRY: tuple[ErrorRegistryEntry, ...] = (
    ErrorRegistryEntry(code="invalid_json", http_status=400, retryable=False),
    ErrorRegistryEntry(code="invalid_request", http_status=400, retryable=False),
    ErrorRegistryEntry(code="invalid_action", http_status=400, retryable=False),
    ErrorRegistryEntry(code="unknown_action", http_status=400, retryable=False),
    ErrorRegistryEntry(code="invalid_args", http_status=400, retryable=False),
    ErrorRegistryEntry(code="request_id_mismatch", http_status=400, retryable=False),
    ErrorRegistryEntry(code="invalid_idempotency_key", http_status=400, retryable=False),
    ErrorRegistryEntry(code="unauthorized", http_status=401, retryable=False),
    ErrorRegistryEntry(code="not_found", http_status=404, retryable=False),
    ErrorRegistryEntry(code="ambiguous_name", http_status=409, retryable=False),
    ErrorRegistryEntry(code="no_confident_match", http_status=409, retryable=False),
    ErrorRegistryEntry(code="link_button_not_pressed", http_status=409, retryable=True),
    ErrorRegistryEntry(code="idempotency_in_progress", http_status=409, retryable=True),
    ErrorRegistryEntry(code="idempotency_key_reuse_mismatch", http_status=409, retryable=False),
    ErrorRegistryEntry(code="bridge_unreachable", http_status=424, retryable=True),
    ErrorRegistryEntry(code="rate_limited", http_status=429, retryable=True),
    ErrorRegistryEntry(code="bridge_rate_limited", http_status=429, retryable=True),
    ErrorRegistryEntry(code="internal_error", http_status=500, retryable="maybe"),
    ErrorRegistryEntry(code="bridge_error", http_status=502, retryable="maybe"),
)

