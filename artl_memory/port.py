# -*- coding: utf-8 -*-
"""存储协议。记忆层只依赖这个形状；实现可以是内存、SQLite，或你自己的后端。

协议分四组，实现一个后端就是实现这四组方法：
    op 链      —— 交互历史，append-only
    mem        —— 按 (owner, designer, project) 隔离的键值记忆
    utterances —— 逐句原话，带说话人，留 N 天后降级
    memory_items / trace —— 统一记忆条目（证据链）与决策轨迹

实现者必读（两个内置后端共同遵守的约定）：
    - 值经 JSON 序列化存储：tuple 读出时会变 list，自定义对象需可自行序列化
    - prune_utterances 的返回值 = 删除条数 + 清正文条数
    - purge_designer 是真删不是标记——这是"被遗忘权"的硬实现

规模与并发的边界（写在明处，不藏着）：
    - InMemoryStore 非线程安全，只适合单线程与测试
    - MemoryService.recall 是 O(n) 全量打分，为可解释性刻意不做索引，
      适用万级以下条目；更大规模请在自己的后端里建索引
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .types import Op


@runtime_checkable
class MemoryStore(Protocol):
    """记忆后端的协议。所有方法按 designer（主人）隔离，这是硬约束。"""

    # ---- op 链 -----------------------------------------------------------
    def append_op(self, op: Op) -> None: ...

    def last_op(self, designer: str) -> Op | None: ...

    def recent_ops(self, designer: str, limit: int = 20, before: float = 0.0) -> list[Op]: ...

    def count_ops(self, designer: str) -> int: ...

    def clear_ops(self, designer: str) -> int: ...

    # ---- mem：按 owner 隔离的键值 ----------------------------------------
    def get_mem(self, owner: str, designer: str, project, key: str, default: Any = None) -> Any: ...

    def set_mem(self, owner: str, designer: str, project, key: str, value: Any,
                confidence: float = 1.0) -> None: ...

    def all_mem(self, owner: str, designer: str, project=None) -> dict: ...

    def snapshot_mem(self, owner: str, designer: str, project=None) -> dict: ...

    def clear_mem(self, owner: str, designer: str) -> int: ...

    def clear_all_mem(self, designer: str) -> int: ...

    # ---- utterances：逐句原话 ---------------------------------------------
    def add_utterance(self, designer: str, row: dict) -> None: ...

    def utterances(self, designer: str, days: int = 7, limit: int = 200) -> list: ...

    def count_utterances(self, designer: str, days: int | None = None) -> int: ...

    def prune_utterances(self, designer: str, days: int = 7) -> int: ...

    # ---- memory_items：统一记忆条目（证据链） -------------------------------
    def add_memory_item(self, item: dict) -> None: ...

    def memory_items(self, designer: str, project: str = "", scope: str = "",
                     kind: str = "", key: str = "", active_only: bool = True,
                     limit: int = 1000) -> list: ...

    def expire_memory_item(self, item_id: str, ts: float, designer: str) -> None: ...

    # ---- trace：决策轨迹 ---------------------------------------------------
    def append_trace(self, designer: str, row: dict) -> None: ...

    def traces(self, designer: str, limit: int = 50, before: float = 0.0) -> list: ...

    # ---- 隐私：把一个人整个抹掉 ---------------------------------------------
    def purge_designer(self, designer: str) -> dict: ...

    # ---- 生命周期 ----------------------------------------------------------
    def close(self) -> None: ...
