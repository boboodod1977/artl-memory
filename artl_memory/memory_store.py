# -*- coding: utf-8 -*-
"""内存实现：零依赖，行为与 SQLite 版一致。

用于测试、试用、以及"先把逻辑跑起来再决定用什么后端"。

注意：本实现不是线程安全的，只适合单线程与测试场景；
要并发请用 SqliteMemoryStore（自带可重入锁）。
"""

from __future__ import annotations

import copy
import json
import time
from contextlib import contextmanager
from typing import Any

from .types import Op


def _json_roundtrip(value: Any) -> Any:
    """与 SQLite 后端同口径：值按 JSON 序列化存储。

    tuple 读出变 list、datetime 变 str——边界写在 README，两个后端
    必须长一样，否则同一段代码换个后端行为就变了。
    """
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


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
        return copy.deepcopy(out)           # 给副本：外部改嵌套值不许污染库

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
        # 按 ts 排序，与 SQLite 后端口径一致（乱序写入不该有两种结果）
        rows.sort(key=lambda o: float(o.ts or 0))
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
        # 按 ts 排序，与 SQLite 后端口径一致（乱序写入不该有两种结果）
        rows.sort(key=lambda r: float(r.get("ts") or 0))
        return copy.deepcopy(rows[-limit:])  # 给副本：外部改不许污染库

    def count_utterances(self, designer: str, days: int | None = None) -> int:
        if days is None:
            return sum(1 for r in self._utt if r.get("designer") == designer)
        cutoff = time.time() - days * 86400
        return sum(1 for r in self._utt
                   if r.get("designer") == designer and float(r.get("ts") or 0) >= cutoff)

    def prune_utterances(self, designer: str, days: int = 7) -> int:
        """超过 N 天：没引用过的整行删，有引用的正文清空（snippet 留着）。

        "有引用"的判定：snippet 非空（写入时标记），或被
        memory_items.source_event 点名（证据链）。被引用的行先用正文
        兜底 snippet 再清正文——证据链不能悬空。
        返回值 = 删除条数 + 清正文条数（与 SQLite 后端口径一致）。
        """
        cutoff = time.time() - days * 86400
        referenced = [str(it.get("source_event") or "")
                      for it in self._items.values()
                      if it.get("designer") == designer and it.get("source_event")]

        def _is_referenced(rid) -> bool:
            rid = str(rid or "")
            # 子串匹配是刻意保守的：误判的代价是多留一条证据，方向安全
            return bool(rid) and any(rid in ref for ref in referenced)

        n = 0
        keep = []
        for r in self._utt:
            if r.get("designer") == designer and float(r.get("ts") or 0) < cutoff:
                if r.get("snippet") or _is_referenced(r.get("id")):
                    if r.get("text"):
                        if not r.get("snippet"):
                            r["snippet"] = str(r.get("text") or "")[:40]
                        r["text"] = ""      # 有引用的留 snippet，正文清掉
                        n += 1
                    # 正文已清过的不再计数：prune 幂等
                else:
                    n += 1
                    continue                # 没引用的整行删掉
            keep.append(r)
        self._utt = keep
        return n

    # ---- 记忆（按 owner/designer/project 隔离） ------------------------------
    @staticmethod
    def _norm_project(project) -> str:
        # None 与 "" 归一为同一个槽，与 SQLite 后端的 _key() 口径一致
        return "" if project is None else str(project)

    def set_mem(self, owner, designer, project, key, value, confidence: float = 1.0) -> None:
        slot = (owner, designer, self._norm_project(project))
        self._mem[slot] = self._mem.get(slot, {})
        self._mem[slot][key] = {"value": _json_roundtrip(value),
                                "confidence": confidence, "ts": time.time()}

    def get_mem(self, owner, designer, project, key, default: Any = None) -> Any:
        row = self._mem.get((owner, designer, self._norm_project(project)), {}).get(key)
        return default if row is None else row["value"]

    def all_mem(self, owner, designer, project=None) -> dict:
        rows = self._mem.get((owner, designer, self._norm_project(project)), {})
        return {k: v["value"] for k, v in rows.items()}

    def snapshot_mem(self, owner, designer, project=None) -> dict:
        # 深拷贝：快照是给人看的，改动快照不许污染库（SQLite 版天然是新对象）
        return copy.deepcopy(
            self._mem.get((owner, designer, self._norm_project(project)), {}))

    # ---- 统一记忆条目（证据链） ----------------------------------------------
    def add_memory_item(self, item: dict) -> None:
        item = dict(item)
        item["value"] = _json_roundtrip(item.get("value"))
        self._items[str(item.get("id"))] = item

    def memory_items(self, designer: str, project: str = "", scope: str = "",
                     kind: str = "", key: str = "", active_only: bool = True,
                     limit: int = 1000, per_key_latest: bool = False) -> list:
        out = []
        best = {}  # per_key_latest 模式：key -> 最新有效行
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
            if per_key_latest:
                k = str(row.get("key") or "")
                if not k or row.get("valid_to"):
                    continue  # 无 key 的是流水不是槽位；保底只保有效行
                if k not in best or float(row.get("valid_from") or 0) > float(
                        best[k].get("valid_from") or 0):
                    best[k] = row
                continue
            out.append(copy.deepcopy(row))  # 深拷贝：外部改嵌套值不许污染库
        if per_key_latest:
            out = [copy.deepcopy(r) for r in best.values()]
            out.sort(key=lambda r: -float(r.get("valid_from") or 0))
            return out  # 保底不看 limit
        out.sort(key=lambda r: -float(r.get("valid_from") or 0))
        # 调用方给多少就是多少，不在这里偷偷设天花板
        return out[:max(1, int(limit))]

    def expire_memory_item(self, item_id: str, ts: float, designer: str) -> None:
        ts = float(ts)
        if ts <= 0:
            # truthy 判断下 valid_to=0 等于"恢复有效"，这是撤销失效的后门
            raise ValueError("expire 的时间戳必须 > 0")
        if not designer:
            raise ValueError("expire 必须指明 designer（属主校验）")
        row = self._items.get(item_id)
        if row is None:
            return
        # 属主校验：拿到 id 不许作废别人的条目
        if row.get("designer") != designer:
            return
        row["valid_to"] = ts

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

    def export_designer(self, designer: str) -> dict:
        """GDPR 数据可携带：这个人的全部数据打包成一份可核验的 dict。

        导出是读取不是脱敏——这是本人的数据副本，配合 purge_designer
        覆盖"查数据、删数据"两项权利。
        """
        import dataclasses
        ops = [dataclasses.asdict(o) if dataclasses.is_dataclass(o) else dict(o)
               for o in self._ops if o.designer == designer]
        mem_rows = []
        for (owner, d, project), rows in self._mem.items():
            if d != designer:
                continue
            for k, v in rows.items():
                row = {"owner": owner, "designer": d, "project": project, "key": k}
                row.update(copy.deepcopy(v))
                mem_rows.append(row)
        utt = [copy.deepcopy(r) for r in self._utt
               if r.get("designer") == designer]
        trace = [copy.deepcopy(r) for r in self._trace
                 if r.get("designer") == designer]
        items = [copy.deepcopy(r) for r in self._items.values()
                 if r.get("designer") == designer]
        items.sort(key=lambda r: float(r.get("valid_from") or 0))
        tables = {"ops": ops, "utterances": utt, "memory": mem_rows,
                  "memory_items": items, "trace": trace}
        return {"format": "artl-memory-export", "format_version": 1,
                "designer": designer, "exported_at": time.time(),
                "tables": tables,
                "counts": {k: len(v) for k, v in tables.items()}}

    # ---- 花名册：这个库里记了哪些人 ------------------------------------------
    def designers(self) -> list[str]:
        out = {o.designer for o in self._ops}
        out |= {slot[1] for slot in self._mem}
        out |= {str(r.get("designer") or "") for r in self._utt}
        out |= {str(r.get("designer") or "") for r in self._trace}
        out |= {str(v.get("designer") or "") for v in self._items.values()}
        out.discard("")  # 空串不是人名，是脏数据
        return sorted(out)

    # ---- 生命周期 -------------------------------------------------------------
    @contextmanager
    def batch(self):
        """内存版没有 fsync 问题，batch 只是保持两后端接口同构的空操作。"""
        yield

    def close(self) -> None:
        pass
