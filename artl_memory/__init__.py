# -*- coding: utf-8 -*-
"""artl-memory — ARTL 家族的记忆层。

逐句原话 · 按人隔离 · 证据链 · 把握度。
It remembers everyone in the room — separately.

    from artl_memory import SqliteMemoryStore, InMemoryStore, MemoryService, MemView

    store = SqliteMemoryStore("memory.db")
    mem = MemoryService(store, designer="heima")
    mem.remember("fact", "预算", "三十万", source="utterance:123", confidence=0.9)
    mem.recall("客厅预算多少")
"""

from .memview import MemView
from .memory_store import InMemoryStore
from .port import MemoryStore
from .service import MemoryItem, MemoryService
from .sqlite import SqliteMemoryStore
from .types import Op

__version__ = "0.3.0"

__all__ = [
    "InMemoryStore",
    "MemoryItem",
    "MemoryService",
    "MemoryStore",
    "MemView",
    "Op",
    "SqliteMemoryStore",
    "__version__",
]
