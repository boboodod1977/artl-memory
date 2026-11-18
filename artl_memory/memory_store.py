# -*- coding: utf-8 -*-
"""内存实现：零依赖，行为与 SQLite 版一致。

用于测试、试用、以及"先把逻辑跑起来再决定用什么后端"。

注意：本实现不是线程安全的，只适合单线程与测试场景；
要并发请用 SqliteMemoryStore（自带可重入锁）。
"""

from __future__ import annotations

import time
from typing import Any

from .types import Op


class InMemoryStore:
    def __init__(self):
        self._ops: list[Op] = []
        self._mem: dict[tuple, dict] = {}
        self._utt: list[dict] = []
        self._trace: list[dict] = []
        self._items: dict[str, dict] = {}

    # ---- 决策轨迹（append-only） -------------------------------------------
    def append_trace(self, designer: str, row: dict) -> None:
        row = dict(row)
        row["designer"] = designer
        self._trace.append(row)

    def traces(self, designer: str, limit: int = 50, before: float = 0.0) -> list:
        rows = [r for r in self._trace if r.get("designer") == designer]
        if before:
            rows = [r for r in rows if float(r.get("ts") or 0) < before]
        out = rows[-limit:]
        out.reverse()                       # 最新在前，跟排查的视角一致
        return out

    # ---- op 链 -------------------------------------------------------------
    def append_op(self, op: Op) -> None:
        if op.parent is None and self._ops:
            prev = self._last_of(op.designer)
            if prev is not None:
                op.parent = prev.id
        self._ops.append(op)

    def _last_of(self, designer: str) -> Op | None:
        for op in reversed(self._ops):
            if op.designer == designer:
                return op
        return None

    def last_op(self, designer: str) -> Op | None:
        return self._last_of(designer)

    def recent_ops(self, designer: str, limit: int = 20, before: float = 0.0) -> list[Op]:
        rows = [o for o in self._ops if o.designer == designer]
        if before:
            rows = [o for o in rows if float(o.ts) < before]
        return rows[-limit:]

    def count_ops(self, designer: str) -> int:
        return sum(1 for o in self._ops if o.designer == designer)

    def clear_ops(self, designer: str) -> int:
        before = len(self._ops)
        self._ops = [o for o in self._ops if o.designer != designer]
        return before - len(self._ops)

    def clear_mem(self, owner: str, designer: str) -> int:
        n = 0
        for key in list(self._mem):
            if key[0] == owner and key[1] == designer:
                n += len(self._mem.pop(key))
        return n

    def clear_all_mem(self, designer: str) -> int:
        """这个人的所有记忆，不分 owner。"""
        n = 0
        for key in list(self._mem):
            if key[1] == designer:
                n += len(self._mem.pop(key))
        return n

    # ---- 逐句（留 N 天后降级） ----------------------------------------------
    def add_utterance(self, designer: str, row: dict) -> None:
        row = dict(row)
        row["designer"] = designer
        self._utt.append(row)

    def utterances(self, designer: str, days: int = 7, limit: int = 200) -> list:
        cutoff = time.time() - days * 86400
        rows = [r for r in self._utt
                if r.get("designer") == designer and float(r.get("ts") or 0) >= cutoff]
        return rows[-limit:]

    def count_utterances(self, designer: str, days: int | None = None) -> int:
        cutoff = time.time() - (days or 3650) * 86400
        return sum(1 for r in self._utt
                   if r.get("designer") == designer and float(r.get("ts") or 0) >= cutoff)

    def prune_utterances(self, designer: str, days: int = 7) -> int:
        """超过 N 天：没引用的整行删，有引用的清正文留 snippet。

        返回值 = 删除条数 + 清正文条数（与 SQLite 后端口径一致）。
        """
        cutoff = time.time() - days * 86400
        n = 0
        keep = []
        for r in self._utt:
            if r.get("designer") == designer and float(r.get("ts") or 0) < cutoff:
                n += 1
                if r.get("snippet"):
                    r["text"] = ""          # 有引用的留 snippet，正文清掉
                else:
                    continue                # 没引用的整行删掉
            keep.append(r)
        self._utt = keep
        return n

    # ---- 记忆（按 owner/designer/project 隔离） ------------------------------
    def set_mem(self, owner, designer, project, key, value, confidence: float = 1.0) -> None:
        slot = (owner, designer, project)
        self._mem[slot] = self._mem.get(slot, {})
        self._mem[slot][key] = {"value": value, "confidence": confidence, "ts": time.time()}

    def get_mem(self, owner, designer, project, key, default: Any = None) -> Any:
        row = self._mem.get((owner, designer, project), {}).get(key)
        return default if row is None else row["value"]

    def all_mem(self, owner, designer, project=None) -> dict:
        rows = self._mem.get((owner, designer, project), {})
        return {k: v["value"] for k, v in rows.items()}

    def snapshot_mem(self, owner, designer, project=None) -> dict:
        return dict(self._mem.get((owner, designer, project), {}))

    # ---- 统一记忆条目（证据链） ----------------------------------------------
    def add_memory_item(self, item: dict) -> None:
        self._items[str(item.get("id"))] = dict(item)

    def memory_items(self, designer: str, project: str = "", scope: str = "",
                     kind: str = "", key: str = "", active_only: bool = True,
                     limit: int = 1000) -> list:
        out = []
        for row in self._items.values():
            if row.get("designer") != designer or str(row.get("project") or "") != project:
                continue
            if scope and row.get("scope") != scope:
                continue
            if kind and row.get("kind") != kind:
                continue
            if key and row.get("key") != key:
                continue
            if active_only and row.get("valid_to"):
                continue
            out.append(dict(row))
        out.sort(key=lambda r: -float(r.get("valid_from") or 0))
        return out[:max(1, min(5000, int(limit)))]

    def expire_memory_item(self, item_id: str, ts: float) -> None:
        if item_id in self._items:
            self._items[item_id]["valid_to"] = float(ts)

    # ---- 隐私：把一个人整个抹掉 ----------------------------------------------
    def purge_designer(self, designer: str) -> dict:
        """删除这个人的全部数据（ops/utterances/memory/memory_items/trace）。

        返回各部分删掉的条数。这是"被遗忘权"的硬实现：不是标记，是真删。
        """
        n_ops = len(self._ops)
        self._ops = [o for o in self._ops if o.designer != designer]
        n_utt = len(self._utt)
        self._utt = [r for r in self._utt if r.get("designer") != designer]
        n_trace = len(self._trace)
        self._trace = [r for r in self._trace if r.get("designer") != designer]
        n_mem = 0
        for key in list(self._mem):
            if key[1] == designer:
                n_mem += len(self._mem.pop(key))
        n_items = len(self._items)
        self._items = {k: v for k, v in self._items.items()
                       if v.get("designer") != designer}
        return {"ops": n_ops - len(self._ops),
                "utterances": n_utt - len(self._utt),
                "memory": n_mem,
                "memory_items": n_items - len(self._items),
                "trace": n_trace - len(self._trace)}

    # ---- 生命周期 -------------------------------------------------------------
    def close(self) -> None:
        pass
