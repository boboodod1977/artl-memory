# -*- coding: utf-8 -*-
"""可选语义召回：把任何 embedding 函数接进 MemoryService(recall_scorer=...)。

核心依然是零依赖的——本模块只做"翻译"：你自带 embed 函数（本地小模型、
任何 embedding API 都行），它包装成 recall_scorer 协议。不装语义插件、
不传 scorer，核心行为一字不变；传了，换说法也能召回，而证据链、原话、
"为什么想起这条"的可解释性仍由核心保管——scorer 只负责找，不负责写。

用法（本地小模型）：
    from artl_memory.contrib import fastembed_scorer
    mem = MemoryService(store, designer="王强",
                        recall_scorer=fastembed_scorer())

用法（任何 embedding API）：
    from artl_memory.contrib import EmbeddingScorer
    def my_embed(texts):  # texts: list[str] -> list[向量]
        return call_your_api(texts)
    mem = MemoryService(store, designer="王强",
                        recall_scorer=EmbeddingScorer(my_embed))
"""

from __future__ import annotations

import math


def _cosine(a: list, b: list) -> float:
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        fx, fy = float(x), float(y)
        dot += fx * fy
        na += fx * fx
        nb += fy * fy
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class EmbeddingScorer:
    """recall_scorer 协议的 embedding 实现。

    embed: callable(list[str]) -> list[向量]，批量接口（一次调用比逐条
    快得多——embedding API 按次/按 token 计费，批量就是省钱）。
    向量按条目 id 缓存：条目内容写入后不可变，缓存是安全的。
    """

    def __init__(self, embed):
        if not callable(embed):
            raise TypeError("embed 必须是 callable(list[str]) -> list[向量]")
        self._embed = embed
        self._vecs: dict = {}

    def __call__(self, query: str, rows: list) -> dict:
        texts = {}
        for r in rows:
            rid = str(r.get("id") or "")
            if rid:
                texts[rid] = ("%s %s" % (r.get("key") or "",
                                         r.get("value") or "")).strip()
        if not texts:
            return {}
        todo = [rid for rid in texts if rid not in self._vecs]
        if todo:
            vecs = self._embed([texts[rid] for rid in todo])
            for rid, v in zip(todo, vecs):
                self._vecs[rid] = list(v)
        qv = list(self._embed([query])[0])
        # 余弦落在 [-1,1]；服务层会 clamp 到 [0,1]，负分自然沉底
        return {rid: _cosine(qv, self._vecs[rid]) for rid in texts}


def fastembed_scorer(model: str = "BAAI/bge-small-zh-v1.5") -> EmbeddingScorer:
    """用 fastembed 本地小模型构造 scorer。

    需要可选依赖：pip install artl-memory[semantic]
    默认 bge-small-zh-v1.5（约 100MB，首次运行自动下载，之后纯离线）。
    """
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise ImportError(
            "语义召回需要 fastembed：pip install artl-memory[semantic]"
        ) from exc
    emb = TextEmbedding(model_name=model)

    def _embed(texts: list) -> list:
        return [list(map(float, vec)) for vec in emb.embed(list(texts))]

    return EmbeddingScorer(_embed)
