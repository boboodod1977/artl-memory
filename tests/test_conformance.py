# -*- coding: utf-8 -*-
"""协议一致性测试：同一套用例，内存版和 SQLite 版必须给出同样的行为。

跑法（两种都支持）：
    python tests/test_conformance.py      # 独立运行，无需 pytest
    pytest tests/                         # 标准 pytest
"""

import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artl_memory import InMemoryStore, MemoryService, MemView, Op, SqliteMemoryStore


def _make_memory():
    return InMemoryStore()


def _make_sqlite():
    return SqliteMemoryStore(Path(tempfile.mkdtemp()) / "t.db")


_BACKENDS = [("memory", _make_memory), ("sqlite", _make_sqlite)]


def pytest_generate_tests(metafunc):
    """让 pytest 自动把每个用例跑在两个后端上。"""
    if "make" in metafunc.fixturenames:
        metafunc.parametrize("make", [m for _, m in _BACKENDS],
                             ids=[n for n, _ in _BACKENDS])


def _op(designer, say, ts):
    return Op(id="op_" + uuid.uuid4().hex[:8], designer=designer, ts=ts,
              say=say, reply="...")


def test_mem_isolation(make):
    """按 owner/designer 隔离：技能只见到自己的，别人的进不来。"""
    s = make()
    s.set_mem("core", "heima", None, "口味", "不吃香菜", 0.9)
    s.set_mem("skill:render", "heima", None, "风格", "奶油风", 0.8)
    s.set_mem("core", "guest", None, "口味", "吃香菜", 0.9)

    view = MemView(s, owner="skill:render", designer="heima")
    assert view.get("风格") == "奶油风"
    assert view.get("口味") is None, "技能不该看到基础记忆"
    assert s.get_mem("core", "heima", None, "口味") == "不吃香菜"
    assert s.get_mem("core", "guest", None, "口味") == "吃香菜"
    assert s.clear_all_mem("heima") >= 2
    assert s.get_mem("core", "guest", None, "口味") == "吃香菜", "清别人不能误伤"


def test_op_chain(make):
    """op 链自动串 parent，按人分页。"""
    s = make()
    s.append_op(_op("heima", "第一句", 1.0))
    s.append_op(_op("heima", "第二句", 2.0))
    s.append_op(_op("heima", "第三句", 3.0))
    ops = s.recent_ops("heima", limit=10)
    assert len(ops) == 3 and ops[-1].parent == ops[-2].id
    assert s.count_ops("heima") == 3
    assert s.count_ops("nobody") == 0


def test_utterances_prune(make):
    """到期降级：没引用的删，有引用的清正文留 snippet。"""
    s = make()
    old = time.time() - 10 * 86400
    s.add_utterance("heima", {"id": "u1", "who": "王强", "text": "预算三十万",
                              "snippet": "", "ts": old})
    s.add_utterance("heima", {"id": "u2", "who": "王强", "text": "石材用云朵拉灰",
                              "snippet": "石材用云朵拉灰", "ts": old})
    s.add_utterance("heima", {"id": "u3", "who": "本人", "text": "记一下",
                              "snippet": "", "ts": time.time()})
    n = s.prune_utterances("heima", days=7)
    assert n == 2, "返回值 = 删除条数 + 清正文条数，两后端同口径"
    rows = {r["id"]: r for r in s.utterances("heima", days=365)}
    assert "u1" not in rows, "没引用的旧话该整行删掉"
    assert rows["u2"]["text"] == "" and rows["u2"]["snippet"], "有引用的留 snippet"
    assert rows["u3"]["text"] == "记一下", "新话不动"


def test_snapshot_ts_is_real(make):
    """snapshot 里的时间戳必须是真实写入时间，不能是占位 0。"""
    s = make()
    before = time.time()
    s.set_mem("core", "heima", None, "k", "v")
    ts = s.snapshot_mem("core", "heima", None)["k"]["ts"]
    assert ts >= before, "时间戳要真实，排查'这条是什么时候记住的'全靠它"


def test_snapshot_cannot_pollute_store(make):
    """改动快照不许污染库：快照是给人看的，不是给库留的后门。"""
    s = make()
    s.set_mem("core", "heima", None, "k", {"nested": 1})
    s.snapshot_mem("core", "heima", None)["k"]["value"]["nested"] = 999
    assert s.get_mem("core", "heima", None, "k") == {"nested": 1}


def test_count_utterances_none_means_all(make):
    """days=None 语义是"全部"，不许暗中被截成 N 天；days=0 两后端同口径。"""
    s = make()
    s.add_utterance("heima", {"id": "old", "who": "w", "text": "t",
                              "ts": time.time() - 4000 * 86400})
    assert s.count_utterances("heima", days=None) == 1, "十年前的也算全部"
    assert s.count_utterances("heima", days=0) == 0, "days=0 就是只看此刻之后"


def test_purge_designer(make):
    """一键抹掉一个人：五类数据全清，别人不受影响。"""
    s = make()
    s.set_mem("core", "heima", None, "口味", "不吃香菜")
    s.set_mem("core", "guest", None, "口味", "吃香菜")
    s.append_op(_op("heima", "你好", 1.0))
    s.append_op(_op("guest", "嗨", 2.0))
    s.add_utterance("heima", {"id": "u1", "who": "本人", "text": "t", "ts": time.time()})
    s.add_memory_item({"id": "m1", "designer": "heima", "project": "",
                       "kind": "fact", "key": "k", "value": 1, "valid_from": 1.0})
    s.append_trace("heima", {"id": "t1", "ts": 1.0, "steps": []})

    out = s.purge_designer("heima")
    assert sum(out.values()) >= 5

    assert s.count_ops("heima") == 0
    assert s.count_utterances("heima") == 0
    assert s.get_mem("core", "heima", None, "口味") is None
    assert s.memory_items("heima") == []
    assert s.traces("heima") == []
    assert s.count_ops("guest") == 1, "抹掉一个人不能误伤别人"
    assert s.get_mem("core", "guest", None, "口味") == "吃香菜"


def test_service_evidence_chain(make):
    """改口：旧值失效进证据链，新值带着 supersedes。"""
    s = make()
    mem = MemoryService(s, designer="heima")
    assert mem.enabled
    a = mem.remember("fact", "王强:预算", "三十万", source="u1", confidence=0.9)
    dup = mem.remember("fact", "王强:预算", "三十万", source="u1", confidence=0.9)
    assert dup["id"] == a["id"], "同值去重"
    b = mem.remember("fact", "王强:预算", "三十五万", source="u2", confidence=0.9)
    assert b["supersedes"] == a["id"]
    hits = mem.recall("王强预算")
    assert hits and hits[0]["value"] == "三十五万"
    assert mem.forget(b["id"])
    assert mem.recall("王强预算") == []


def test_service_decay_prefers_fresh(make):
    """召回排序：相关度 × 把握度 × 时间衰减。"""
    s = make()
    mem = MemoryService(s, designer="heima")
    mem.remember("fact", "喜好", "喜欢暖木色", confidence=0.9,
                 ts=time.time() - 50 * 86400)
    mem.remember("fact", "喜好", "喜欢暖木和自然光", confidence=0.9, ts=time.time())
    hits = mem.recall("喜欢暖木")
    assert hits[0]["value"] == "喜欢暖木和自然光", "新鲜的该排前面"


def test_recall_hits_single_char_key(make):
    """单字 key（人名"周"）也要能被问到：key 命中加权，不许冤枉地说想不起来。"""
    s = make()
    mem = MemoryService(s, designer="heima")
    mem.remember("note", "周", "开发时间", confidence=0.9, ts=time.time())
    assert mem.recall("周讲了什么"), "完整 key 在查询里，必须命中"
    assert mem.recall("周"), "短查询落在 key 里，必须命中"
    assert not mem.recall("李讲了什么"), "不相关的人不能乱中"


def test_sqlite_reopen_after_close():
    """连接被关后自动重开：后台线程拿到旧 store 不鞭尸。"""
    tmp = Path(tempfile.mkdtemp()) / "r.db"
    s = SqliteMemoryStore(tmp)
    s.set_mem("core", "heima", None, "k", "v")
    s.close()
    assert s.closed
    assert s.get_mem("core", "heima", None, "k") == "v", "关了要自己能重开"
    s.close()


def test_sqlite_schema_version():
    """schema 带版本号，以后迁移有依据。"""
    import sqlite3
    tmp = Path(tempfile.mkdtemp()) / "v.db"
    s = SqliteMemoryStore(tmp)
    s.close()
    v = sqlite3.connect(str(tmp)).execute("PRAGMA user_version").fetchone()[0]
    assert v >= 1, "新库必须打上 schema 版本"


def test_per_key_latest(make):
    """槽位保底查询：每个非空 key 只回最新一条有效，无 key 流水不参与。"""
    s = make()
    mem = MemoryService(s, designer="d")
    mem.remember("fact", "预算", "三十万", ts=1.0)
    mem.remember("fact", "预算", "四十万", ts=2.0)   # 替代旧值
    mem.remember("fact", "口味", "不吃香菜", ts=3.0)
    mem.remember("fact", "", "无槽流水", ts=4.0)
    rows = s.memory_items("d", "", per_key_latest=True)
    got = {str(r.get("key")): r.get("value") for r in rows}
    assert got == {"预算": "四十万", "口味": "不吃香菜"}, \
        "每个槽只留最新有效、无 key 不进保底: %r" % got


def test_export_designer(make):
    """GDPR 导出：五表数据全在、只含本人的、条目解析成 API 形态。"""
    s = make()
    mem = MemoryService(s, designer="d")
    mem.remember("fact", "预算", "三十万", ts=1.0, source="utterance:u1")
    mem.remember("fact", "预算", "四十万", ts=2.0, source="utterance:u2")
    s.add_utterance("d", {"id": "u1", "text": "预算三十万", "ts": 1.0})
    s.append_op(_op("d", "第一句", 1.0))
    s.append_trace("d", {"id": "t1", "ts": 1.0, "kind": "turn", "steps": "[]"})
    s.set_mem("core", "d", None, "口味", "不吃香菜", 0.9)
    MemoryService(s, designer="other").remember("fact", "秘密", "别人的", ts=1.0)

    data = s.export_designer("d")
    assert data["format"] == "artl-memory-export" and data["designer"] == "d"
    c = data["counts"]
    assert c["memory_items"] == 2, "改口的旧条也在导出里（证据链不丢）"
    assert c["utterances"] == 1 and c["ops"] == 1 and c["trace"] == 1
    assert c["memory"] == 1
    items = data["tables"]["memory_items"]
    assert {r["value"] for r in items} == {"三十万", "四十万"}, \
        "value_json 必须解析成 API 形态"
    blob = json.dumps(data, ensure_ascii=False)
    assert "别人的" not in blob, "导出不许带出别人的数据"


def test_designers(make):
    """花名册：五张表出现过的 designer 都在名单里，purge 后消失。"""
    s = make()
    assert s.designers() == [], "空库的花名册必须是空的"
    s.add_utterance("alice", {"id": "u1", "text": "hi", "ts": time.time()})
    s.set_mem("core", "bob", None, "k", "v")
    s.append_op(_op("carol", "...", time.time()))
    s.append_trace("dave", {"id": "t1", "ts": time.time(), "steps": []})
    MemoryService(s, designer="erin").remember("fact", "k", "v")
    assert s.designers() == ["alice", "bob", "carol", "dave", "erin"]
    s.purge_designer("alice")
    assert s.designers() == ["bob", "carol", "dave", "erin"], \
        "被抹掉的人不许留在花名册里"


def main():
    n_pass = n_fail = 0
    backend_tests = (test_mem_isolation, test_op_chain, test_utterances_prune,
                     test_snapshot_ts_is_real, test_snapshot_cannot_pollute_store,
                     test_count_utterances_none_means_all, test_purge_designer,
                     test_service_evidence_chain, test_service_decay_prefers_fresh,
                     test_recall_hits_single_char_key, test_per_key_latest,
                     test_export_designer, test_designers)
    for name, make in _BACKENDS:
        for fn in backend_tests:
            try:
                fn(make)
                n_pass += 1
                print(f"  PASS {fn.__name__} [{name}]")
            except AssertionError as exc:
                n_fail += 1
                print(f"  FAIL {fn.__name__} [{name}]: {exc}")
    for fn in (test_sqlite_reopen_after_close, test_sqlite_schema_version):
        try:
            fn()
            n_pass += 1
            print(f"  PASS {fn.__name__} [sqlite]")
        except AssertionError as exc:
            n_fail += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"\n{n_pass} passed, {n_fail} failed")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
