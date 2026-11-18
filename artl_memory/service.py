# -*- coding: utf-8 -*-
"""统一记忆条目：scope、来源、把握度、冲突替代、到期失效。

这是服务层；持久化留在存储协议后面。本模块零 I/O。

设计原则：
    - 失效（expire）不是删除——证据链留在 supersedes 和 valid_to 里
    - 召回 = 相关度 × 把握度 × 时间衰减，每个因子都可解释
    - 不做向量检索：bigram 重叠是刻意的，你随时能看清"它为什么想起这条"
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass


@dataclass
class MemoryItem:
    id: str
    designer: str
    project: str
    scope: str
    kind: str
    key: str
    value: object
    confidence: float
    source_event: str = ""
    valid_from: float = 0.0
    valid_to: float | None = None
    supersedes: str = ""


def _bigrams(s: str) -> set:
    s = re.sub(r"\s+", "", str(s or ""))
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


class MemoryService:
    def __init__(self, store, designer: str, project: str = "", clock=time.time):
        self._store = store
        self.designer = str(designer or "")
        self.project = str(project or "")
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return hasattr(self._store, "add_memory_item") and hasattr(self._store, "memory_items")

    def _items(self, scope: str = "", kind: str = "", key: str = "",
               active_only: bool = True, limit: int = 1000) -> list:
        if not self.enabled:
            return []
        return self._store.memory_items(self.designer, self.project, scope=scope,
                                        kind=kind, key=key,
                                        active_only=active_only, limit=limit)

    def remember(self, kind: str, key: str, value, scope: str = "facts",
                 source: str = "", confidence: float = 0.6,
                 ts: float | None = None) -> dict | None:
        """同值去重；值变了就让旧条目失效并记为被新条目替代。"""
        if not self.enabled:
            return None
        now = float(ts if ts is not None else self._clock())
        for row in self._items(scope=scope, kind=kind):
            if row.get("value") == value:
                return row
        rows = self._items(scope=scope, kind=kind, key=key)
        for row in rows:
            if row.get("value") == value:
                return row
        # 同 key 的旧条目可能不止一条（比如从旧系统导入的）：全部失效，
        # supersedes 指向最近的一条（rows 按 valid_from 倒序，第一条最新）。
        supersedes = ""
        if rows:
            supersedes = str(rows[0].get("id") or "")
            for row in rows:
                self._store.expire_memory_item(row["id"], now)
        item = {
            "id": "mem_" + uuid.uuid4().hex[:12],
            "designer": self.designer, "project": self.project,
            "scope": str(scope or "facts"), "kind": str(kind or "fact"),
            "key": str(key or ""), "value": value,
            "confidence": round(float(confidence or 0.6), 2),
            "source_event": str(source or "")[:200],
            "valid_from": now, "valid_to": None, "supersedes": supersedes,
        }
        self._store.add_memory_item(item)
        return item

    def recall(self, query: str, scope: str = "", limit: int = 5,
               kind: str = "") -> list:
        """O(n) 全量打分，为可解释性刻意不做索引；适用万级以下条目。

        打分因子全在明处：bigram 重叠 × 把握度 × 时间衰减，外加一条
        key 命中加权——查询里带着完整的 key（如人名"周"），或查询很短
        且落在 key 里（如"周"），直接给强分。没有这条，单字 key 永远
        凑不出 bigram，"周讲了什么"会冤枉地说想不起来。
        """
        rows = self._items(scope=scope, kind=kind, active_only=True, limit=2000)
        if not rows:
            return []
        query_str = re.sub(r"\s+", "", str(query or ""))
        q = _bigrams(query_str) if query_str else set()
        now = float(self._clock())
        scored = []
        for row in rows:
            key = str(row.get("key") or "")
            body = "%s %s" % (key, row.get("value") or "")
            f = _bigrams(body)
            overlap = len(q & f) if q else 1
            # key 命中加权：完整 key 出现在查询里，或短查询落在 key 里
            if key and query_str and (key in query_str
                                      or (len(query_str) <= 2 and query_str in key)):
                overlap += 2 * max(1, len(_bigrams(key)))
            if q and overlap == 0:
                continue
            age = max(0.0, now - float(row.get("valid_from") or now)) / 86400.0
            decay = max(0.4, 1.0 - age / 60.0)
            scored.append((overlap * float(row.get("confidence") or 0.6) * decay, row))
        scored.sort(key=lambda x: -x[0])
        return [row for _, row in scored[:max(1, limit)]]

    def forget(self, item_id: str) -> bool:
        if not self.enabled:
            return False
        rows = [r for r in self._items(active_only=True)
                if str(r.get("id") or "") == str(item_id)]
        if not rows:
            return False
        self._store.expire_memory_item(item_id, float(self._clock()))
        return True

    def import_items(self, items: list, scope: str = "facts", source: str = "legacy") -> int:
        """把旧的 fact 字典幂等地导入统一条目表。"""
        n = 0
        for raw in items or []:
            if not isinstance(raw, dict) or not raw.get("text"):
                continue
            got = self.remember(
                kind=str(raw.get("kind") or "fact"),
                key=str(raw.get("key") or ""),
                value=str(raw.get("text") or ""),
                scope=scope, source=source,
                confidence=float(raw.get("confidence") or 0.6),
                ts=float(raw.get("ts") or self._clock()),
            )
            if got:
                n += 1
        return n

    def import_relation(self, relation: dict, source: str = "legacy.relation") -> int:
        """把旧的关系字典幂等地导入 relation scope。"""
        n = 0
        if not isinstance(relation, dict):
            return 0
        for key, value in relation.items():
            if value in (None, "", [], {}):
                continue
            got = self.remember("relation", str(key), value, scope="relation",
                                source=source, confidence=0.8)
            if got:
                n += 1
        return n

    def import_profile(self, profile: dict, source: str = "legacy.profile") -> int:
        """把旧的画像（preferences/lessons 列表）幂等地导入 experience scope。"""
        n = 0
        if not isinstance(profile, dict):
            return 0
        for key in ("preferences", "lessons"):
            for i, value in enumerate(profile.get(key) or []):
                if not value:
                    continue
                got = self.remember("profile:" + key, "%s:%d" % (key, i),
                                    str(value), scope="experience", source=source,
                                    confidence=0.6)
                if got:
                    n += 1
        return n

    def import_mastery(self, rows: list, source: str = "legacy.mastery") -> int:
        """把旧的掌握度记录幂等地导入 mastery scope。"""
        n = 0
        for cap, row in rows or []:
            got = self.remember("mastery", str(cap), row, scope="mastery",
                                source=source, confidence=0.6)
            if got:
                n += 1
        return n
