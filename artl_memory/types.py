# -*- coding: utf-8 -*-
"""基础数据结构。纯数据，无逻辑，无 I/O。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Op:
    """一次交互的记录。整条 op 链就是历史、审计轨迹、撤销栈。

    撤销 = 把指针挪到 parent；分支 = 复制一条 parent；回看 = 顺着链往上走。
    """

    id: str
    designer: str
    ts: float
    say: str
    reply: str
    skill: str | None = None
    parent: str | None = None
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    verdict: dict = field(default_factory=dict)
    ok: bool = True
