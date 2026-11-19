# -*- coding: utf-8 -*-
"""统一记忆条目：scope、来源、把握度、冲突替代、到期失效。

这是服务层；持久化留在存储协议后面。本模块零 I/O。

设计原则：
    - 失效（expire）不是删除——证据链留在 supersedes 和 valid_to 里
    - 召回 = 相关度 × 把握度 × 时间衰减，每个因子都可解释
    - 不做向量检索：bigram 重叠是刻意的，你随时能看清"它为什么想起这条"
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path


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
    def __init__(self, store, designer: str, project: str = "", clock=time.time,
                 recall_window: int = 10000, recall_scorer=None,
                 semantic_weight: float = 3.0, semantic_min: float = 0.5):
        self._store = store
        self.designer = str(designer or "")
        self.project = str(project or "")
        self._clock = clock
        # 召回时的最大扫描条数。README 承诺"适用万级以下"，默认值对齐这个
        # 承诺；超过这个规模请显式调大，而不是被静默截断。
        self.recall_window = int(recall_window) if recall_window else 10000
        # 可选语义插口：callable(query, rows) -> {id: 0..1 分数}。
        # 它只能给库里已有的候选行改排序分——造不出新记忆，
        # 证据链和"它为什么想起这条"的可解释性不被污染。
        self._scorer = recall_scorer
        # semantic_weight：语义满分相当于几个 bigram 命中；semantic_min：
        # 字词零重叠时，语义分达到这个门槛才把条目捞进结果。
        self.semantic_weight = float(semantic_weight)
        self.semantic_min = float(semantic_min)
        # remember 的 查重-失效-写入 是一整条 check-then-act：不加锁，
        # 两个线程同 key 并发会各写一条、supersedes 全断。
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return hasattr(self._store, "add_memory_item") and hasattr(self._store, "memory_items")

    def _items(self, scope: str = "", kind: str = "", key: str = "",
               active_only: bool = True, limit: int = 0) -> list:
        if not self.enabled:
            return []
        return self._store.memory_items(self.designer, self.project, scope=scope,
                                        kind=kind, key=key,
                                        active_only=active_only,
                                        limit=limit or self.recall_window)

    def remember(self, kind: str, key: str, value, scope: str = "facts",
                 source: str = "", confidence: float = 0.6,
                 ts: float | None = None) -> dict | None:
        """同值去重；值变了就让旧条目失效并记为被新条目替代。

        返回的条目带一个 "created" 标记：True=本次新写入，
        False=命中去重返回的是旧条目（import_* 靠它数"新增"）。
        """
        if not self.enabled:
            return None
        with self._lock:  # check-then-act 全程原子，并发下同 key 不双写
            now = float(ts if ts is not None else self._clock())
            for attempt in range(5):  # 撞跨进程唯一约束时重读收敛再重试
                # 去重必须限定同 key：只按 scope+kind 查会把"别人记过同样的值"
                # 也命中，导致第二个人的记忆被吞掉并返回第一个人的条目。
                rows = self._items(scope=scope, kind=kind, key=key)
                # 空 key 在后端等于"不过滤"，这里按精确 key 再筛一遍，
                # 否则无 key 事实会去重到别人带 key 的条目上（P0-1 的变种）
                rows = [r for r in rows if str(r.get("key") or "") == str(key or "")]
                dup = None
                for row in rows:
                    if row.get("value") == value:
                        dup = row
                        break
                if dup is not None:
                    dup = dict(dup)
                    dup["created"] = False
                    return dup
                # 同 key 的旧条目可能不止一条（比如从旧系统导入的）：全部失效，
                # supersedes 指向最近的一条（rows 按 valid_from 倒序，第一条最新）。
                # 但只对非空 key 生效：空 key 是"无 key 事实"，不是同一个槽——
                # 否则 import 一批无 key 事实会互相顶掉，永远只有最后一条活着。
                supersedes = ""
                if rows and key:
                    supersedes = str(rows[0].get("id") or "")
                    for row in rows:
                        self._store.expire_memory_item(row["id"], now,
                                                       designer=self.designer)
                item = {
                    "id": "mem_" + uuid.uuid4().hex[:12],
                    "designer": self.designer, "project": self.project,
                    "scope": str(scope or "facts"), "kind": str(kind or "fact"),
                    "key": str(key or ""), "value": value,
                    "confidence": round(0.6 if confidence is None else float(confidence), 2),
                    "source_event": str(source or "")[:200],
                    "valid_from": now, "valid_to": None, "supersedes": supersedes,
                }
                try:
                    self._store.add_memory_item(item)
                except Exception as exc:
                    # SQLite 唯一索引（同槽位只允许一条有效）被另一个进程
                    # 抢先触发：回炉重读，按上面的去重/替代逻辑收敛。
                    # 用异常名判断，避免服务层反过来 import 后端模块。
                    # 退避带抖动：高负载机器上两进程同步重试会反复互撞
                    if type(exc).__name__ == "ConflictError" and attempt < 4:
                        time.sleep(0.01 * (attempt + 1)
                                   + random.random() * 0.01)
                        continue
                    raise
                item["created"] = True
                return item

    def remember_many(self, items: list, scope: str = "facts",
                      source: str = "batch") -> int:
        """批量写入：整个批次一次落盘，返回新增条数。

        逐条 remember 每条 commit 一次，5 万条要约 77 秒（瓶颈是 fsync，
        不是 SQL）；走这个接口，SQLite 后端整批只 commit 一次。
        后端没有 batch()（比如内存版）时退化为逐条，语义不变。
        """
        from contextlib import nullcontext
        batch = getattr(self._store, "batch", None)
        ctx = batch() if callable(batch) else nullcontext()
        n = 0
        with ctx:
            for raw in items or []:
                got = self.remember(
                    kind=str(raw.get("kind") or "fact"),
                    key=str(raw.get("key") or ""),
                    value=raw.get("value"),
                    scope=str(raw.get("scope") or scope),
                    source=str(raw.get("source") or source),
                    confidence=raw.get("confidence", 0.6),
                    ts=raw.get("ts"),
                )
                if got and got.get("created"):
                    n += 1
        return n

    def recall(self, query: str, scope: str = "", limit: int = 5,
               kind: str = "") -> list:
        """O(n) 全量打分，为可解释性刻意不做索引；适用万级以下条目。

        打分因子全在明处：相关度 × 把握度 × 时间衰减。相关度 = 字词分
        （bigram 重叠 + key 命中加权）+ 语义分（可选 recall_scorer，
        满分相当于 semantic_weight 个 bigram）。key 命中加权：查询里
        带着完整的 key（如人名"周"），或查询很短且落在 key 里，给小分
        保命。注意单字 key 只加 0.5：够它不被 overlap==0 筛掉，但压不过
        正文的真实命中——否则"周末讲什么"会把 key="周" 的无关条目顶到第一。

        字词零重叠的条目，只有语义分 ≥ semantic_min 才会被捞进来——
        换说法召回走的就是这条路，没装 scorer 时行为和以前一模一样。

        扫描上限是 self.recall_window（默认一万，可显式调大），不再硬编码；
        窗口之外，每个非空 key 的最新有效条目保底参与打分——"这个槽现在
        的值"不许被流水挤掉。每条结果带 recall_detail：word / semantic /
        confidence / decay 四个因子分列，"为什么想起这条"随时可查。
        """
        if int(limit) <= 0:
            return []
        query_str = re.sub(r"\s+", "", str(query or ""))
        if not query_str:
            # 空查询返回空——"你想找什么"没有答案时，
            # 返回"最新几条"是假装理解了你的意图
            return []
        rows = self._items(scope=scope, kind=kind, active_only=True,
                           limit=self.recall_window)
        if len(rows) >= self.recall_window:
            # 窗口满了意味着最老的条目可能没参与打分——必须喊出来，
            # 不能让"它没想起来"和"它根本没看到"无法区分
            warnings.warn("recall 扫描已满 recall_window=%d，最老的条目可能未参与打分；"
                          "请调大 MemoryService(recall_window=...)" % self.recall_window,
                          RuntimeWarning, stacklevel=2)
        # 槽位保底：每个非空 key 的最新有效条目永远参与打分，不受窗口截断
        floor = self._store.memory_items(self.designer, self.project,
                                         scope=scope, kind=kind,
                                         active_only=True,
                                         per_key_latest=True)
        seen = {str(r.get("id") or "") for r in rows}
        rows = list(rows) + [r for r in floor
                             if str(r.get("id") or "") not in seen]
        if not rows:
            return []
        # 语义插口：scorer 只能给已有候选打分，给陌生 id 打分直接丢弃——
        # 重排可以，注入不行。scorer 挂了退化为纯字词，召回不许跟着死。
        sem: dict = {}
        if self._scorer is not None:
            try:
                raw = self._scorer(query_str, [dict(r) for r in rows]) or {}
                for rid, s in dict(raw).items():
                    try:
                        sem[str(rid)] = min(1.0, max(0.0, float(s)))
                    except (TypeError, ValueError):
                        continue
            except Exception as exc:
                warnings.warn("recall_scorer 出错（%s），本轮退化为纯字词打分"
                              % exc, RuntimeWarning, stacklevel=2)
                sem = {}
        q = _bigrams(query_str)
        now = float(self._clock())
        scored = []
        for row in rows:
            key = str(row.get("key") or "")
            body = "%s %s" % (key, row.get("value") or "")
            f = _bigrams(body)
            overlap = len(q & f)
            # key 命中加权：精确相等的 key 加得最多（查"周"时"周先生"
            # 必须压过"周报""周末"）；多字 key 完整出现在查询里给强分；
            # 单字 key 被包含只给小分保命——不被 overlap==0 筛掉，
            # 但压不过正文的真实命中。
            if key:
                if key == query_str:
                    overlap += 2 * max(1, len(_bigrams(key))) + 1
                elif len(key) > 1 and key in query_str:
                    overlap += 2 * len(_bigrams(key))
                elif key in query_str or (len(query_str) <= 2 and query_str in key):
                    overlap += 0.5
            s = sem.get(str(row.get("id") or ""), 0.0)
            if overlap == 0 and s < self.semantic_min:
                continue
            relevance = overlap + (self.semantic_weight * s
                                   if self._scorer is not None else 0.0)
            age = max(0.0, now - float(row.get("valid_from") or now)) / 86400.0
            decay = max(0.4, 1.0 - age / 60.0)
            conf = row.get("confidence")
            conf_f = 0.6 if conf is None else float(conf)
            row2 = dict(row)
            row2["recall_detail"] = {
                "word": float(overlap), "semantic": s,
                "confidence": conf_f, "decay": round(decay, 6),
            }
            scored.append((relevance * conf_f * decay, row2))
        scored.sort(key=lambda x: -x[0])
        return [row for _, row in scored[:int(limit)]]

    def export_designer(self, path: str | Path | None = None) -> dict:
        """GDPR 数据可携带：这个人五张表的全部数据打包成一份 dict。

        给了 path 就同时写成 UTF-8 JSON 文件（交付给本人的格式）。
        查数据（export）+ 删数据（store.purge_designer）两项权利成对。
        """
        if not self.enabled:
            return {}
        data = self._store.export_designer(self.designer)
        if path:
            Path(path).write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8")
        return data

    def evidence_chain(self, item_id: str) -> list:
        """顺着 supersedes 往回走：[当前条目, ..., 最早条目]。

        每一环都带 source_event 和 valid_to——"它现在这么认为，之前那么
        认为，依据分别是哪句原话"一条链说清。找不到 id 返回空列表。
        """
        if not self.enabled:
            return []
        by_id = {str(r.get("id") or ""): r
                 for r in self._items(active_only=False)}
        chain, seen = [], set()
        cur = by_id.get(str(item_id or ""))
        while cur is not None and cur.get("id") not in seen:
            seen.add(cur.get("id"))
            chain.append(cur)
            cur = by_id.get(str(cur.get("supersedes") or ""))
        return chain

    def forget(self, item_id: str) -> bool:
        if not self.enabled:
            return False
        rows = [r for r in self._items(active_only=True)
                if str(r.get("id") or "") == str(item_id)]
        if not rows:
            return False
        self._store.expire_memory_item(item_id, float(self._clock()),
                                       designer=self.designer)
        return True

    def import_items(self, items: list, scope: str = "facts", source: str = "legacy") -> int:
        """把旧的 fact 字典幂等地导入统一条目表。

        返回值是**新增条数**：去重命中的旧条目不计（重复导入返回 0）。
        """
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
            if got and got.get("created"):
                n += 1
        return n

    def import_relation(self, relation: dict, source: str = "legacy.relation") -> int:
        """把旧的关系字典幂等地导入 relation scope。返回新增条数。"""
        n = 0
        if not isinstance(relation, dict):
            return 0
        for key, value in relation.items():
            if value in (None, "", [], {}):
                continue
            got = self.remember("relation", str(key), value, scope="relation",
                                source=source, confidence=0.8)
            if got and got.get("created"):
                n += 1
        return n

    def import_profile(self, profile: dict, source: str = "legacy.profile") -> int:
        """把旧的画像（preferences/lessons 列表）幂等地导入 experience scope。
        返回新增条数。

        key 用值的内容哈希而不是列表序号：源列表顺序变了不该重复插入，
        序号 key 会把"同一条偏好换了个位置"当成新事实。
        """
        n = 0
        if not isinstance(profile, dict):
            return 0
        for key in ("preferences", "lessons"):
            for value in profile.get(key) or []:
                if not value:
                    continue
                digest = hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:12]
                got = self.remember("profile:" + key, "%s:%s" % (key, digest),
                                    str(value), scope="experience", source=source,
                                    confidence=0.6)
                if got and got.get("created"):
                    n += 1
        return n

    def import_mastery(self, rows: list, source: str = "legacy.mastery") -> int:
        """把旧的掌握度记录幂等地导入 mastery scope。返回新增条数。"""
        n = 0
        for cap, row in rows or []:
            got = self.remember("mastery", str(cap), row, scope="mastery",
                                source=source, confidence=0.6)
            if got and got.get("created"):
                n += 1
        return n
