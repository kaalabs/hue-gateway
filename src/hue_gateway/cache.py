from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any


def normalize_name(value: str) -> str:
    return " ".join(value.strip().lower().split())


@dataclass
class CachedResource:
    rid: str
    rtype: str
    name: str | None
    name_norm: str | None
    data: dict[str, Any]


class ResourceCache:
    def __init__(self) -> None:
        self._by_rid: dict[str, CachedResource] = {}
        self._name_to_rids: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self._rid_to_name_norm: dict[str, str | None] = {}

    def upsert(self, *, rid: str, rtype: str, name: str | None, data: dict[str, Any]) -> None:
        prev = self._by_rid.get(rid)
        if prev and prev.name_norm:
            self._name_to_rids[prev.rtype][prev.name_norm].discard(rid)

        name_norm = normalize_name(name) if isinstance(name, str) and name.strip() else None
        if name_norm:
            self._name_to_rids[rtype][name_norm].add(rid)

        self._rid_to_name_norm[rid] = name_norm
        self._by_rid[rid] = CachedResource(rid=rid, rtype=rtype, name=name, name_norm=name_norm, data=data)

    def delete(self, *, rid: str) -> None:
        prev = self._by_rid.pop(rid, None)
        if prev and prev.name_norm:
            self._name_to_rids[prev.rtype][prev.name_norm].discard(rid)
        self._rid_to_name_norm.pop(rid, None)

    def get(self, rid: str) -> CachedResource | None:
        return self._by_rid.get(rid)

