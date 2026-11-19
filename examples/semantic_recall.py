# -*- coding: utf-8 -*-
"""语义召回插口示例：换说法也能想起来，核心依然零依赖。

跑法：
    python examples/semantic_recall.py

你会看到：
    1. 不装 scorer：查"装修大概多少钱"召不回「预算=客厅要花四十万」
       （字词零重叠，这是默认行为，钉死在测试里）
    2. 装上 scorer：同一条查询把它捞回来
    3. recall_detail 四因子分列：为什么想起这条，装了语义也说得清

本例用的是一个玩具 embed 函数（按关键词命中构造向量），只为演示插口
协议，离线可跑。真实场景换成：

    from artl_memory.contrib import fastembed_scorer
    mem = MemoryService(store, designer="王强",
                        recall_scorer=fastembed_scorer())
    # 需要 pip install artl-memory[semantic]，本地 bge-small-zh，纯离线

或者接任何 embedding API：
    EmbeddingScorer(lambda texts: your_api.embed(texts))
"""

import sys
import tempfile
from pathlib import Path

# Windows 老控制台（GBK / cp1252）打印中文会炸，把 stdout 切到 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artl_memory import MemoryService, SqliteMemoryStore
from artl_memory.contrib import EmbeddingScorer

# --- 玩具 embed：8 个话题维度，词命中对应维度 +1 -----------------------
_TOPICS = ["钱", "吃", "行", "住", "玩", "人", "事", "物"]
_LEX = {"钱": "钱", "预算": "钱", "装修": "钱", "花": "钱", "客厅": "住",
        "香菜": "吃", "口味": "吃", "爬": "玩", "周末": "玩"}


def toy_embed(texts):
    vecs = []
    for t in texts:
        v = [0.0] * len(_TOPICS)
        for word, topic in _LEX.items():
            if word in str(t):
                v[_TOPICS.index(topic)] += 1.0
        vecs.append(v)
    return vecs


def main():
    store = SqliteMemoryStore(Path(tempfile.mkdtemp()) / "sem.db")
    plain = MemoryService(store, designer="王强")
    plain.remember("fact", "预算", "客厅要花四十万",
                   source="utterance:wq-007", confidence=0.9)
    plain.remember("fact", "口味", "不吃香菜",
                   source="utterance:wq-009", confidence=0.8)

    q = "装修大概多少钱"
    print("查询: %s\n" % q)
    hits = plain.recall(q)
    print("1) 不装 scorer（默认纯字词）: %s"
          % ("召回 %d 条" % len(hits) if hits else "找不到——零重叠，诚实的"))

    mem = MemoryService(store, designer="王强",
                        recall_scorer=EmbeddingScorer(toy_embed))
    hits = mem.recall(q)
    print("\n2) 装上 EmbeddingScorer: 召回 %d 条" % len(hits))
    for h in hits:
        d = h["recall_detail"]
        print("   %s = %s" % (h["key"], h["value"]))
        print("   为什么想起它: word=%.1f semantic=%.2f confidence=%.2f decay=%.2f"
              % (d["word"], d["semantic"], d["confidence"], d["decay"]))
        print("   原话出处: %s" % h["source_event"])

    print("\n语义只负责找：原话、证据链、把握度还是核心管的那些。")


if __name__ == "__main__":
    main()
