from __future__ import annotations

import os
import time
from typing import Any

import aiosqlite


class Database:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        dir_name = os.path.dirname(self._db_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA synchronous=NORMAL;")
        await self._init_schema()
        await self._conn.commit()

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("Database not connected")
        return self._conn

    async def _init_schema(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at INTEGER NOT NULL
            );
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency (
              credential_fingerprint TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              action TEXT NOT NULL,
              request_hash TEXT NOT NULL,
              status TEXT NOT NULL,
              response_status_code INTEGER,
              response_json TEXT,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              PRIMARY KEY (credential_fingerprint, idempotency_key)
            );
            """
        )
        await self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_idempotency_expires_at
            ON idempotency (expires_at);
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resources (
              rid TEXT PRIMARY KEY,
              rtype TEXT NOT NULL,
              name TEXT,
              json TEXT NOT NULL,
              updated_at INTEGER NOT NULL
            );
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS name_index (
              rtype TEXT NOT NULL,
              name_norm TEXT NOT NULL,
              rid TEXT NOT NULL,
              PRIMARY KEY (rtype, name_norm, rid)
            );
            """
        )
        await self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_resources_rtype ON resources (rtype);
            """
        )
        await self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_name_index_rtype_name ON name_index (rtype, name_norm);
            """
        )

    async def get_setting(self, key: str) -> str | None:
        async with self.conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return str(row[0])

    async def get_setting_int(self, key: str, default: int = 0) -> int:
        value = await self.get_setting(key)
        if value is None:
            return int(default)
        try:
            return int(value)
        except ValueError:
            return int(default)

    async def set_setting(self, key: str, value: str) -> None:
        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, now),
        )
        await self.conn.commit()

    async def increment_setting_int(self, key: str) -> int:
        now = int(time.time())
        await self.conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, '1', ?)
            ON CONFLICT(key) DO UPDATE SET value=CAST(settings.value AS INTEGER) + 1, updated_at=excluded.updated_at
            """,
            (key, now),
        )
        await self.conn.commit()
        return await self.get_setting_int(key, default=0)

    async def commit(self) -> None:
        await self.conn.commit()

    async def upsert_resource(
        self,
        *,
        rid: str,
        rtype: str,
        name: str | None,
        json_text: str,
        updated_at: int | None = None,
    ) -> None:
        now = int(time.time()) if updated_at is None else updated_at
        await self.conn.execute(
            """
            INSERT INTO resources (rid, rtype, name, json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(rid) DO UPDATE SET
              rtype=excluded.rtype,
              name=excluded.name,
              json=excluded.json,
              updated_at=excluded.updated_at
            """,
            (rid, rtype, name, json_text, now),
        )

    async def delete_name_index_for_rid(self, rid: str) -> None:
        await self.conn.execute("DELETE FROM name_index WHERE rid = ?", (rid,))

    async def insert_name_index(self, *, rtype: str, name_norm: str, rid: str) -> None:
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO name_index (rtype, name_norm, rid)
            VALUES (?, ?, ?)
            """,
            (rtype, name_norm, rid),
        )

    async def delete_resource(self, rid: str) -> None:
        await self.delete_name_index_for_rid(rid)
        await self.conn.execute("DELETE FROM resources WHERE rid = ?", (rid,))

    async def get_resource(self, rid: str) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT json FROM resources WHERE rid = ?",
            (rid,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        import json

        try:
            return json.loads(row[0])
        except Exception:
            return None

    async def list_name_candidates(self, *, rtype: str) -> list[tuple[str, str, str | None]]:
        """
        Returns: [(name_norm, rid, name_display), ...]
        """
        async with self.conn.execute(
            """
            SELECT ni.name_norm, ni.rid, r.name
            FROM name_index ni
            LEFT JOIN resources r ON r.rid = ni.rid
            WHERE ni.rtype = ?
            """,
            (rtype,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [(str(name_norm), str(rid), str(name) if name is not None else None) for name_norm, rid, name in rows]

    async def list_resources(self, *, rtype: str) -> list[dict[str, Any]]:
        import json

        async with self.conn.execute(
            "SELECT json FROM resources WHERE rtype = ?",
            (rtype,),
        ) as cursor:
            rows = await cursor.fetchall()
        out: list[dict[str, Any]] = []
        for (json_text,) in rows:
            try:
                obj = json.loads(json_text)
            except Exception:
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out

    async def rebuild_name_index(self) -> None:
        await self.conn.execute("DELETE FROM name_index")
        async with self.conn.execute(
            "SELECT rid, rtype, name FROM resources WHERE name IS NOT NULL"
        ) as cursor:
            rows = await cursor.fetchall()
        for rid, rtype, name in rows:
            name_norm = " ".join(str(name).strip().lower().split())
            if not name_norm:
                continue
            await self.insert_name_index(rtype=str(rtype), name_norm=name_norm, rid=str(rid))
        await self.conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
