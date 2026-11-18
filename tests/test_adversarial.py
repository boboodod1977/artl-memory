# -*- coding: utf-8 -*-
"""对抗性测试：专挑文档没承诺的暗处下手。

一致性测试（test_conformance.py）管"说到做到"，这里管"没说也不许炸"：
注入串、怪字符、空查询、超老数据、purge 幂等、规模下的召回速度。

跑法：
    python tests/test_adversarial.py     # 独立运行，无需 pytest
    pytest tests/                        # 标准 pytest
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artl_memory import InMemoryStore, MemoryService, SqliteMemoryStore


def _make_memory():
    return InMemoryStore()


def _make_sqlite():
    return SqliteMemoryStore(Path(tempfile.mkdtemp()) / "q.db")


_BACKENDS = [("memory", _make_memory), ("sqlite", _make_sqlite)]


def pytest_generate_tests(metafunc):
    if "make" in metafunc.fixturenames:
        metafunc.parametrize("make", [m for _, m in _BACKENDS],
                             ids=[n for n, _ in _BACKENDS])


def test_special_chars_roundtrip(make):
    """引号、反斜杠、换行、制表符、emoji：原样进，原样出。"""
    s = make()
    row = {"id": "i1", "who": "引号'\"\\混合", "text": "换行\n回车\r制表\temoji🎉",
           "snippet": "", "ts": time.time(), "conf": 0.5}
    s.add_utterance("d", row)
    got = s.utterances("d", days=1)[0]
    assert got["text"] == row["text"] and got["who"] == row["who"]


def test_injection_string_is_just_data(make):
    """SQL 注入串对它来说就是一段普通文字。"""
    evil = "'; DROP TABLE memory_items; --"
    s = make()
    mem = MemoryService(s, designer="d")
    mem.remember("fact", evil, evil, confidence=0.9, ts=time.time())
    hits = mem.recall(evil)
    assert hits and hits[0]["value"] == evil
    assert mem.recall(""), "表必须还在"


def test_recall_empty_query_ranks_all(make):
    """空查询 = 不筛选，纯按 把握度×衰减 排序。"""
    s = make()
    mem = MemoryService(s, designer="d")
    mem.remember("fact", "a", "甲", confidence=0.9, ts=time.time())
    mem.remember("fact", "b", "乙", confidence=0.5, ts=time.time())
    hits = mem.recall("")
    assert [h["key"] for h in hits] == ["a", "b"]


def test_purge_idempotent_and_surgical(make):
    """purge 第二次是空转；隔壁的人一根汗毛不动。"""
    s = make()
    m1 = MemoryService(s, designer="甲")
    m2 = MemoryService(s, designer="乙")
    m1.remember("fact", "预算", "三十万", ts=time.time())
    m2.remember("fact", "预算", "五十万", ts=time.time())
    s.purge_designer("甲")
    assert sum(s.purge_designer("甲").values()) == 0, "第二次 purge 该是空转"
    assert m2.recall("预算"), "purge 甲不许误伤乙"
    assert m1.recall("预算") == [], "被 purge 的人必须真的查无此人"


def test_recall_perf_2000_items():
    """O(n) 承诺万级以下可用：2000 条召回必须够快。

    只测内存版：SQLite 版的瓶颈在写入（逐条 commit），那是磁盘的事；
    召回路径两后端同构。
    """
    s = InMemoryStore()
    mem = MemoryService(s, designer="d")
    for i in range(2000):
        mem.remember("fact", "key%d" % (i % 50), "值%d" % i,
                     confidence=0.6, ts=time.time() - i)
    t0 = time.time()
    assert mem.recall("值1999")
    assert time.time() - t0 < 0.5, "2000 条召回不该超过半秒"


_TESTS = [v for k, v in sorted(globals().items())
          if k.startswith("test_") and callable(v)]


def main():
    n_pass = n_fail = 0
    for fn in _TESTS:
        if fn is test_recall_perf_2000_items:
            variants = [("memory", _make_memory)]
        else:
            variants = _BACKENDS
        for name, make in variants:
            try:
                fn(make) if fn is not test_recall_perf_2000_items else fn()
                n_pass += 1
                print(f"  PASS {fn.__name__} [{name}]")
            except AssertionError as exc:
                n_fail += 1
                print(f"  FAIL {fn.__name__} [{name}]: {exc}")
    print(f"\n{n_pass} passed, {n_fail} failed")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
