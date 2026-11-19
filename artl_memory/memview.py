# -*- coding: utf-8 -*-
"""MemView：一个被限定的记忆视图。

技能/插件拿到的是 `MemView(owner="skill:xxx")`——它**看不到**基础记忆，
也看不到别的技能。这不是靠约定，是靠它手里没有那个入口：

- 不保存 store 本体，只留四个绑定好的方法（get/set/all/snapshot），
  参数里的 owner/designer/project 在构造时就焊死了；
- owner / designer / project 是只读属性，配上 __slots__，
  想改道去读别人的记忆，连赋值这一步都过不去。
"""

from __future__ import annotations

from typing import Any


class MemView:
    __slots__ = ("_owner", "_designer", "_project", "_fns")

    def __init__(self, store, owner: str, designer: str, project=None):
        self._owner = str(owner)
        self._designer = str(designer)
        self._project = project
        # 只留包了一层闭包的方法引用，不挂 store 本体、不挂绑定方法
        # （绑定方法的 __self__ 一跳就能摸回 store）。身份在构造时焊死。
        g, st, al, sn = store.get_mem, store.set_mem, store.all_mem, store.snapshot_mem
        o, d, p = self._owner, self._designer, self._project
        self._fns = {
            "get": lambda key, default=None: g(o, d, p, key, default),
            "set": lambda key, value, confidence=1.0: st(o, d, p, key, value, confidence),
            "all": lambda: al(o, d, p),
            "snapshot": lambda: sn(o, d, p),
        }
        # 诚实声明：这堵墙是 API 面的——正常使用拿不到别人的入口；
        # 但同进程内没有密码学意义的隔离（闭包细胞/gc 总能挖回去）。
        # 要防恶意代码，请上进程边界。

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def designer(self) -> str:
        return self._designer

    @property
    def project(self):
        return self._project

    def get(self, key: str, default: Any = None) -> Any:
        return self._fns["get"](key, default)

    def put(self, key: str, value: Any, confidence: float = 1.0) -> None:
        self._fns["set"](key, value, confidence)

    def all(self) -> dict:
        return self._fns["all"]()

    def snapshot(self) -> dict:
        return self._fns["snapshot"]()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MemView {self._owner} designer={self._designer}>"
