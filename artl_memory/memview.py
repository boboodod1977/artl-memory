# -*- coding: utf-8 -*-
"""MemView：一个被限定的记忆视图。

技能/插件拿到的是 `MemView(owner="skill:xxx")`——它**看不到**基础记忆，
也看不到别的技能。这不是靠约定，是靠它手里没有那个入口。
"""

from __future__ import annotations

from typing import Any


class MemView:
    def __init__(self, store, owner: str, designer: str, project=None):
        self._store = store
        self.owner = owner
        self.designer = designer
        self.project = project

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get_mem(self.owner, self.designer, self.project, key, default)

    def put(self, key: str, value: Any, confidence: float = 1.0) -> None:
        self._store.set_mem(self.owner, self.designer, self.project, key, value, confidence)

    def all(self) -> dict:
        return self._store.all_mem(self.owner, self.designer, self.project)

    def snapshot(self) -> dict:
        return self._store.snapshot_mem(self.owner, self.designer, self.project)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MemView {self.owner} designer={self.designer}>"
