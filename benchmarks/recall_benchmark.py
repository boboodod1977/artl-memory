# -*- coding: utf-8 -*-
"""记忆库召回质量公开评测：同一数据集、同一判定口径，各家自己跑。

跑法：
    python benchmarks/recall_benchmark.py                # 只测 artl-memory（零依赖）
    python benchmarks/recall_benchmark.py --with-mem0    # 加测 Mem0（需自行配置，见 README）
    python benchmarks/recall_benchmark.py --json out.json

公平性约定（改数据集请先读 README）：
    - 三个真实人物、12 条写入、10 个查询，全部写死在本文件里，谁都能审
    - 判定用字符串包含，不用模型当裁判——裁判不该是选手
    - 每个 adapter 拿到一模一样的写入序列和查询序列
    - 结果原文贴进 benchmarks/README.md，附跑的时间和版本
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------- 数据集
# (key, 原话, source_event, ts)
WRITES = {
    "王强": [
        ("预算", "客厅装修预算是三十万", "utterance:wq-001", 1.0),
        ("预算", "改一下，客厅预算三十五万", "utterance:wq-002", 2.0),  # 改口
        ("口味", "我不吃香菜", "utterance:wq-003", 3.0),
        ("行程", "下周三去杭州出差", "utterance:wq-004", 4.0),
    ],
    "李四": [
        ("预算", "书房预算八万", "utterance:ls-001", 1.5),
        ("口味", "我不吃辣", "utterance:ls-002", 2.5),
        ("宠物", "家里养了两只猫", "utterance:ls-003", 3.5),
    ],
    "赵六": [
        ("口味", "香菜多放", "utterance:zl-001", 1.2),
        ("预算", "全屋预算一百万", "utterance:zl-002", 2.8),
    ],
}

# (查询, 问谁, 期望值, 结果里不许出现, 类型)
QUERIES = [
    ("客厅预算多少", "王强", "三十五万", ["三十万", "八万", "一百万"], "字面命中"),
    ("王强的预算", "王强", "三十五万", ["三十万"], "改口时效"),  # 旧值不许在
    ("预算", "李四", "八万", ["三十五万", "一百万"], "按人隔离"),
    ("口味", "李四", "不吃辣", ["香菜"], "按人隔离"),
    ("行程", "王强", "杭州", ["赵六", "李四"], "字面命中"),
    ("装修大概要花多少钱", "王强", "三十五万", [], "换说法"),     # 字面匹配会丢
    ("能吃辣吗", "李四", "不吃辣", [], "换说法"),
    ("出差去哪", "王强", "杭州", [], "换说法"),
    ("宠物", "李四", "两只猫", ["出差"], "字面命中"),
    ("预算", "赵六", "一百万", ["三十五万", "八万"], "按人隔离"),
]

# 类型 -> 计分桶；字面库在"换说法"上的失分是预期行为，单独成列不混算
KINDS = ["字面命中", "改口时效", "按人隔离", "换说法"]


class ArtlMemoryAdapter:
    """artl-memory：默认纯字词；--semantic 时装玩具 scorer 演示插口。"""

    def __init__(self, semantic: bool = False):
        from artl_memory import MemoryService, SqliteMemoryStore
        self._db = Path(tempfile.mkdtemp()) / "bench.db"
        self._store = SqliteMemoryStore(self._db)
        self._services = {}
        self._semantic = semantic
        self.name = ("artl-memory %s (+toy-scorer)" if semantic
                     else "artl-memory %s（默认字词）")
        self._version = None
        from artl_memory import __version__
        self._version = __version__

    def _svc(self, person):
        from artl_memory import MemoryService
        if person not in self._services:
            scorer = _toy_scorer() if self._semantic else None
            self._services[person] = MemoryService(
                self._store, designer=person, recall_scorer=scorer)
        return self._services[person]

    def add(self, person, key, text, source, ts):
        self._svc(person).remember("fact", key, text, source=source, ts=ts)

    def recall(self, person, query, limit=3):
        return [str(h.get("value")) for h in self._svc(person).recall(query, limit=limit)]

    def label(self):
        return self.name % self._version


def _toy_scorer():
    """演示插口用的玩具 embed（同 examples/semantic_recall.py）——只为证明
    换说法能救回来；严肃的语义分数请接真 embedding（见 README）。"""
    from artl_memory.contrib import EmbeddingScorer
    topics = ["钱", "吃", "行", "住", "玩"]
    lex = {"钱": "钱", "预算": "钱", "装修": "钱", "花": "钱", "客厅": "住",
           "辣": "吃", "口味": "吃", "香菜": "吃", "出差": "行", "行程": "行",
           "杭州": "行", "猫": "住", "宠物": "住", "书房": "住", "全屋": "住"}

    def embed(texts):
        out = []
        for t in texts:
            v = [0.0] * len(topics)
            for w, tp in lex.items():
                if w in str(t):
                    v[topics.index(tp)] += 1.0
            out.append(v)
        return out

    return EmbeddingScorer(embed)


class Mem0Adapter:
    """Mem0 对照：只有显式 --with-mem0 才跑。

    两种模式，标签里写死是哪种：
    - **抽取模式**（它的设计默认）：设 MEM0_BENCH_LLM_API_KEY（可选
      MEM0_BENCH_LLM_BASE_URL / MEM0_BENCH_LLM_MODEL）就走 LLM 抽取，
      任何 OpenAI 兼容端点都行。
    - **裸向量模式**（infer=False）：没 key 时的兜底。LLM 端点被故意
      指向 127.0.0.1:1 这个黑洞——它敢发一次请求就立刻报错，用这个
      证明整条链路零 LLM 调用。这一行测的是它的存储+检索栈。
    embedder 用本地 fastembed（bge-small-zh-v1.5），向量库本地 qdrant，
    版本号打印在结果里。依赖没装好就报错退出，不静默跳过。
    """

    def __init__(self):
        import os
        try:
            from mem0 import Memory
        except ImportError as exc:
            raise SystemExit("--with-mem0 需要 pip install mem0ai fastembed：%s" % exc)
        tmp = tempfile.mkdtemp(prefix="mem0-bench-")
        api_key = os.environ.get("MEM0_BENCH_LLM_API_KEY", "").strip()
        self._infer = bool(api_key)
        llm_cfg = {
            "provider": "openai",
            "config": {
                "api_key": api_key or "sk-blackhole",
                # 没 key 时指向黑洞：任何一次 LLM 调用都会立刻炸出来
                "openai_base_url": os.environ.get(
                    "MEM0_BENCH_LLM_BASE_URL",
                    "https://api.openai.com/v1" if api_key else "http://127.0.0.1:1"),
                "model": os.environ.get("MEM0_BENCH_LLM_MODEL",
                                        "gpt-4o-mini" if api_key else "unused"),
                "temperature": 0,
            },
        }
        self._m = Memory.from_config({
            "llm": llm_cfg,
            "embedder": {"provider": "fastembed",
                         "config": {"model": "BAAI/bge-small-zh-v1.5"}},
            "vector_store": {"provider": "qdrant",
                             "config": {"collection_name": "bench",
                                        "path": tmp,
                                        "embedding_model_dims": 512}},
        })
        import importlib.metadata
        try:
            self._version = importlib.metadata.version("mem0ai")
        except Exception:
            self._version = "unknown"

    def add(self, person, key, text, source, ts):
        self._m.add(text, user_id=person, infer=self._infer,
                    metadata={"key": key, "ts": ts, "source": source})

    def recall(self, person, query, limit=3):
        # mem0 2.x：按人过滤走 filters={"user_id": ...}，不再是顶层参数
        res = self._m.search(query, filters={"user_id": person}, top_k=limit)
        rows = res.get("results", res) if isinstance(res, dict) else res
        return [str(r.get("memory", r)) for r in rows][:limit]

    def label(self):
        mode = "LLM 抽取" if self._infer else "infer=False 裸向量"
        return "Mem0 %s（%s，bge-small-zh）" % (self._version, mode)


def run(adapter):
    for person, rows in WRITES.items():
        for key, text, source, ts in rows:
            adapter.add(person, key, text, source, ts)
    per_kind = {k: [0, 0] for k in KINDS}  # kind -> [命中, 总数]
    violations = 0
    details = []
    for q, person, expect, must_not, kind in QUERIES:
        got = adapter.recall(person, q)
        joined = " | ".join(got)
        hit = bool(got) and expect in got[0]
        bad = [m for m in must_not if m in joined]
        violations += len(bad)
        per_kind[kind][1] += 1
        per_kind[kind][0] += 1 if hit else 0
        details.append({"q": q, "person": person, "kind": kind,
                        "expect": expect, "got": got, "hit": hit,
                        "violations": bad})
    total_hit = sum(v[0] for v in per_kind.values())
    total = sum(v[1] for v in per_kind.values())
    return {"adapter": adapter.label(), "ts": time.strftime("%Y-%m-%d %H:%M"),
            "top1": "%d/%d" % (total_hit, total),
            "per_kind": {k: "%d/%d" % tuple(v) for k, v in per_kind.items()},
            "violations": violations, "details": details}


def to_markdown(results):
    lines = ["| 记忆库 | top1 命中 | 字面命中 | 改口时效 | 按人隔离 | 换说法 | 违规串供 |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        p = r["per_kind"]
        lines.append("| %s | %s | %s | %s | %s | %s | %d |" % (
            r["adapter"], r["top1"], p["字面命中"], p["改口时效"],
            p["按人隔离"], p["换说法"], r["violations"]))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--semantic", action="store_true",
                    help="artl-memory 加测一档：装玩具 scorer 演示语义插口")
    ap.add_argument("--with-mem0", action="store_true")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    adapters = [ArtlMemoryAdapter()]
    if args.semantic:
        adapters.append(ArtlMemoryAdapter(semantic=True))
    if args.with_mem0:
        adapters.append(Mem0Adapter())

    results = [run(a) for a in adapters]
    md = to_markdown(results)
    print(md)
    print("\n明细（未命中的查询）：")
    for r in results:
        for d in r["details"]:
            if not d["hit"] or d["violations"]:
                print("  [%s] %s 问%s: 期望含 %r，实得 %r%s" % (
                    r["adapter"], d["person"], d["q"], d["expect"],
                    d["got"][:2],
                    " 违规: %s" % d["violations"] if d["violations"] else ""))
    if args.json:
        Path(args.json).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\nJSON 已写入 %s" % args.json)


if __name__ == "__main__":
    main()
