# -*- coding: utf-8 -*-
"""MCP server：把 artl-memory 直接挂进 Claude Desktop / Cursor / 任何 MCP 客户端。

核心保持零依赖——这是个可选挂件，要用先装：

    pip install "artl-memory[mcp]"

Claude Desktop / Cursor 配置（把 db 路径换成你自己的）：

    {
      "mcpServers": {
        "artl-memory": {
          "command": "uvx",
          "args": ["--from", "artl-memory[mcp]", "artl-memory-mcp",
                   "--db", "/abs/path/memory.db"]
        }
      }
    }

也可以直接跑：

    python -m artl_memory.contrib.mcp_server --db /abs/path/memory.db

工具即承诺：remember 只记交给它的话，每条带来源和把握度；recall 把
"为什么想起这条"的四个因子一并返回；改口留下证据链；purge_person 是
物理删除（secure_delete + VACUUM + WAL 截断），不是标记。
"""

# 注意：本模块不能用 `from __future__ import annotations`——FastMCP 靠
# 检查注解对象生成工具的 JSON Schema，字符串化的注解会让它崩溃。

import argparse
import os
import sys
import threading

from ..service import MemoryService
from ..sqlite import SqliteMemoryStore

# 召回合并用的语义权重默认值，与 MemoryService 的默认一致。
# contrib 层不重写打分逻辑，只用 recall_detail 里的因子还原总分做跨人排序。
_DEFAULT_SEMANTIC_WEIGHT = 3.0


class MemoryBase:
    """MCP 会话期间共享的记忆库句柄：一个 db 文件，按人缓存 service。"""

    def __init__(self, db_path):
        self.db_path = str(db_path)
        self._store = None
        self._lock = threading.RLock()
        self._services: dict[str, MemoryService] = {}

    @property
    def store(self) -> SqliteMemoryStore:
        with self._lock:
            if self._store is None:
                self._store = SqliteMemoryStore(self.db_path)
            return self._store

    def service(self, person: str) -> MemoryService:
        person = str(person or "").strip()
        if not person:
            raise ValueError("person 不能为空——这个库的边界就是按人记账")
        with self._lock:
            svc = self._services.get(person)
            if svc is None:
                svc = MemoryService(self.store, designer=person)
                self._services[person] = svc
            return svc

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                self._store.close()
                self._store = None
                self._services.clear()


# ---------------------------------------------------------------------------
# 工具实现：纯函数 + MemoryBase，不 import mcp 包，单测不需要装任何额外依赖
# ---------------------------------------------------------------------------

def tool_remember(base: MemoryBase, person: str, key: str, value,
                  kind: str = "fact", source: str = "",
                  confidence: float = 0.6) -> dict:
    item = base.service(person).remember(kind, key, value,
                                         source=source, confidence=confidence)
    if item is None:
        return {"ok": False, "reason": "store backend does not support memory items"}
    return {"ok": True, "id": item["id"], "person": person,
            "created": bool(item.get("created")),
            "supersedes": item.get("supersedes") or ""}


def _final_score(row: dict) -> float:
    """用 recall_detail 的公开因子还原总分：相关度 × 把握度 × 时间衰减。"""
    d = row.get("recall_detail") or {}
    relevance = float(d.get("word") or 0) + _DEFAULT_SEMANTIC_WEIGHT * float(
        d.get("semantic") or 0)
    return relevance * float(d.get("confidence") or 0.6) * float(d.get("decay") or 1.0)


def tool_recall(base: MemoryBase, person: str, query: str,
                limit: int = 5) -> list:
    return base.service(person).recall(query, limit=limit)


def tool_recall_all(base: MemoryBase, query: str, limit: int = 5) -> list:
    """跨人召回：问全屋。每条结果带 person 字段，按还原总分排序。"""
    out = []
    for person in base.store.designers():
        for row in base.service(person).recall(query, limit=limit):
            row = dict(row)
            row["person"] = person
            out.append(row)
    out.sort(key=_final_score, reverse=True)
    return out[:int(limit)]


def tool_list_people(base: MemoryBase) -> list:
    return base.store.designers()


def tool_evidence_chain(base: MemoryBase, person: str, item_id: str) -> list:
    return base.service(person).evidence_chain(item_id)


def tool_forget(base: MemoryBase, person: str, item_id: str) -> dict:
    ok = base.service(person).forget(item_id)
    return {"ok": ok}


def tool_purge_person(base: MemoryBase, person: str) -> dict:
    person = str(person or "").strip()
    if not person:
        raise ValueError("person 不能为空")
    return base.store.purge_designer(person)


def tool_export_person(base: MemoryBase, person: str) -> dict:
    return base.service(person).export_designer()


# ---------------------------------------------------------------------------
# MCP 协议层：装了 mcp 包才会走到这里
# ---------------------------------------------------------------------------

def create_app(base: MemoryBase, name: str = "artl-memory"):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - 只在没装 extra 时触发
        raise SystemExit(
            "artl-memory 的 MCP 挂件需要 mcp 包：pip install \"artl-memory[mcp]\""
        ) from exc

    app = FastMCP(name)

    @app.tool()
    def remember(person: str, key: str, value: str, kind: str = "fact",
                 source: str = "", confidence: float = 0.6) -> dict:
        """Store a fact attributed to one person. Same person + same key +
        same value de-duplicates; a changed value expires the old entry but
        keeps it in the evidence chain (nothing is silently overwritten).
        `source` should point at where this came from (e.g. an utterance id
        or a message reference); `confidence` is 0..1. Memories are strictly
        per-person: one person's entries are invisible to everyone else."""
        return tool_remember(base, person, key, value, kind, source, confidence)

    @app.tool()
    def recall(person: str, query: str, limit: int = 5) -> list:
        """Recall what ONE person said matching `query`. Each result carries
        value, confidence, source_event and recall_detail (word / semantic /
        confidence / decay factors) — you can always see WHY an entry came
        back. Scopes to this person only; use recall_all to ask the room."""
        return tool_recall(base, person, query, limit)

    @app.tool()
    def recall_all(query: str, limit: int = 5) -> list:
        """Recall across EVERYONE in the room. Each result is tagged with the
        person it belongs to; ranking merges per-person scores. Isolation is
        still enforced inside the store — this just queries each person."""
        return tool_recall_all(base, query, limit)

    @app.tool()
    def list_people() -> list:
        """List every person this memory base knows about (anyone who appears
        in utterances, memory items, ops, trace or key-value memory)."""
        return tool_list_people(base)

    @app.tool()
    def evidence_chain(person: str, item_id: str) -> list:
        """Walk the supersedes chain of one memory item: [current, ..., oldest].
        Each link carries its source_event and valid_to — 'what is believed
        now, what was believed before, and on whose words'."""
        return tool_evidence_chain(base, person, item_id)

    @app.tool()
    def forget(person: str, item_id: str) -> dict:
        """Expire one memory item. NOT a deletion: the entry stays in the
        evidence chain marked expired. For real deletion use purge_person."""
        return tool_forget(base, person, item_id)

    @app.tool()
    def purge_person(person: str) -> dict:
        """IRREVERSIBLE: physically delete EVERYTHING about this person from
        all five tables (secure_delete + VACUUM + WAL truncate — a byte-scan
        of the database file finds nothing afterwards). This is the GDPR
        right-to-be-forgotten hard implementation, not a flag. Returns
        per-table deleted row counts."""
        return tool_purge_person(base, person)

    @app.tool()
    def export_person(person: str) -> dict:
        """Export everything stored about one person as a verifiable dict
        (GDPR data portability: the person's own copy, not a redaction).
        Pair with purge_person to cover the rights to access and erasure."""
        return tool_export_person(base, person)

    return app


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="artl-memory-mcp",
        description="artl-memory MCP server — multi-person memory for agents")
    parser.add_argument(
        "--db", default=os.environ.get("ARTL_MEMORY_DB", "artl-memory.db"),
        help="SQLite 文件路径（默认环境变量 ARTL_MEMORY_DB，否则 ./artl-memory.db）")
    parser.add_argument(
        "--transport", default=os.environ.get("ARTL_MEMORY_TRANSPORT", "stdio"),
        choices=["stdio", "sse", "streamable-http"],
        help="MCP 传输方式（默认 stdio，Claude Desktop / Cursor 用它）")
    args = parser.parse_args(argv)

    base = MemoryBase(args.db)
    app = create_app(base)
    try:
        app.run(transport=args.transport)
    finally:
        base.close()


if __name__ == "__main__":
    main()
