from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from hue_gateway.db import Database
from hue_gateway.security import AuthContext


def credential_fingerprint(auth: AuthContext) -> str:
    h = hashlib.sha256()
    h.update(auth.scheme.encode("utf-8"))
    h.update(b":")
    h.update(auth.credential.encode("utf-8"))
    return h.hexdigest()


def request_hash(*, action: str, args: Any) -> str:
    # Stable hash for idempotency comparisons.
    canonical = json.dumps({"action": action, "args": args}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IdempotencyRecord:
    credential_fingerprint: str
    idempotency_key: str
    action: str
    request_hash: str
    status: str  # in_progress | completed
    response_status_code: int | None
    response_json: str | None
    created_at: int
    updated_at: int
    expires_at: int


async def get_record(*, db: Database, credential_fp: str, key: str) -> IdempotencyRecord | None:
    async with db.conn.execute(
        """
        SELECT credential_fingerprint, idempotency_key, action, request_hash, status,
               response_status_code, response_json, created_at, updated_at, expires_at
        FROM idempotency
        WHERE credential_fingerprint = ? AND idempotency_key = ?
        """,
        (credential_fp, key),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    return IdempotencyRecord(
        credential_fingerprint=str(row[0]),
        idempotency_key=str(row[1]),
        action=str(row[2]),
        request_hash=str(row[3]),
        status=str(row[4]),
        response_status_code=int(row[5]) if row[5] is not None else None,
        response_json=str(row[6]) if row[6] is not None else None,
        created_at=int(row[7]),
        updated_at=int(row[8]),
        expires_at=int(row[9]),
    )


async def mark_in_progress(
    *,
    db: Database,
    credential_fp: str,
    key: str,
    action: str,
    req_hash: str,
    ttl_seconds: int,
) -> tuple[IdempotencyRecord, bool]:
    now = int(time.time())
    expires_at = now + max(1, int(ttl_seconds))
    cur = await db.conn.execute(
        """
        INSERT OR IGNORE INTO idempotency (
          credential_fingerprint, idempotency_key, action, request_hash, status,
          response_status_code, response_json, created_at, updated_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
        """,
        (credential_fp, key, action, req_hash, "in_progress", now, now, expires_at),
    )
    inserted = cur.rowcount == 1
    await db.commit()
    rec = await get_record(db=db, credential_fp=credential_fp, key=key)
    if not rec:
        # Should never happen, but fail safe by returning an in-progress record.
        return (
            IdempotencyRecord(
            credential_fingerprint=credential_fp,
            idempotency_key=key,
            action=action,
            request_hash=req_hash,
            status="in_progress",
            response_status_code=None,
            response_json=None,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            ),
            inserted,
        )
    return rec, inserted


async def mark_completed(
    *,
    db: Database,
    credential_fp: str,
    key: str,
    action: str,
    req_hash: str,
    status_code: int,
    response_obj: dict[str, Any],
    ttl_seconds: int,
) -> None:
    now = int(time.time())
    expires_at = now + max(1, int(ttl_seconds))
    response_json = json.dumps(response_obj, separators=(",", ":"), ensure_ascii=False)
    await db.conn.execute(
        """
        UPDATE idempotency
        SET action = ?, request_hash = ?, status = ?,
            response_status_code = ?, response_json = ?, updated_at = ?, expires_at = ?
        WHERE credential_fingerprint = ? AND idempotency_key = ?
        """,
        (action, req_hash, "completed", int(status_code), response_json, now, expires_at, credential_fp, key),
    )
    await db.commit()


async def cleanup_expired(*, db: Database, max_rows: int = 5000) -> int:
    now = int(time.time())
    # Delete expired rows first.
    cur = await db.conn.execute("DELETE FROM idempotency WHERE expires_at <= ?", (now,))
    deleted = cur.rowcount if cur.rowcount is not None else 0

    # Hard cap: delete oldest rows beyond max_rows.
    async with db.conn.execute("SELECT COUNT(*) FROM idempotency") as cursor:
        row = await cursor.fetchone()
    count = int(row[0]) if row and row[0] is not None else 0
    if count > max_rows:
        to_delete = count - max_rows
        await db.conn.execute(
            """
            DELETE FROM idempotency
            WHERE rowid IN (
              SELECT rowid FROM idempotency
              ORDER BY updated_at ASC
              LIMIT ?
            )
            """,
            (to_delete,),
        )
        deleted += to_delete

    await db.commit()
    return deleted


async def cleanup_loop(*, db: Database, interval_seconds: int = 60) -> None:
    while True:
        try:
            await cleanup_expired(db=db)
        except Exception:
            # Best-effort housekeeping only.
            pass
        await time_sleep(interval_seconds)


async def time_sleep(seconds: int) -> None:
    import asyncio

    await asyncio.sleep(max(1, int(seconds)))
