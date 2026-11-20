# -*- coding: utf-8 -*-
"""回归测试：钉死三个修过的坑，不许回潮。

P0-1  remember 同值跨人吞记忆（service.py 去重没限定 key）
P0-2  prune 把被 memory_items 引用的原话整行删掉，证据链悬空
P1-4  purge_designer 只 DELETE 不 VACUUM，库里明文残留（仅 SQLite）

跑法：
    python tests/test_regressions.py    # 独立运行，无需 pytest
    pytest tests/                       # 标准 pytest
"""

import sys
import tempfile
import time
import warnings
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


# ---------------- P0-1：同值跨人吞记忆 ----------------

def test_same_value_different_people_both_stored(make):
    """两个人记同一个值：必须各自入账，不能返回同一个条目。

    会议室场景的原型：张三和李四都说"预算三十万"，系统给两人各记
    一条。修复前去重循环没限定 key，李四那条被吞，还误返回张三的。
    """
    mem = MemoryService(make(), designer="d")
    a = mem.remember("fact", "张三", "预算三十万", confidence=0.9, ts=time.time())
    b = mem.remember("fact", "李四", "预算三十万", confidence=0.9, ts=time.time())
    assert a["id"] != b["id"], "同值不同人必须生成两个条目"
    items = mem._items(scope="facts", kind="fact", active_only=False)
    assert len(items) == 2, "两条都必须真实落库"


def test_same_value_same_person_deduped(make):
    """同一个人重复记同一个值：仍然去重（旧语义不能修丢）。"""
    mem = MemoryService(make(), designer="d")
    a = mem.remember("fact", "张三", "预算三十万", ts=time.time())
    b = mem.remember("fact", "张三", "预算三十万", ts=time.time())
    assert a["id"] == b["id"], "同人同值应去重"


# ---------------- P0-2：prune 断证据链 ----------------

def test_prune_keeps_referenced_utterance(make):
    """被记忆条目引用的原话到期：清正文、留 snippet，绝不整行删。

    修复前删除条件只看 snippet 空不空，跟 source_event 零联动——
    证据链说断就断。
    """
    s = make()
    old = time.time() - 10 * 86400
    s.add_utterance("d", {"id": "u-ref", "who": "王强",
                          "text": "尾款下周五之前一定到账",
                          "snippet": "", "ts": old})
    mem = MemoryService(s, designer="d")
    item = mem.remember("promise", "王强", "尾款下周五之前到账",
                        source="u-ref", confidence=0.8, ts=old)
    assert item["source_event"] == "u-ref"

    n = s.prune_utterances("d", days=7)
    assert n == 1, "这一条被引用：清正文，不删行"
    rows = {r["id"]: r for r in s.utterances("d", days=365)}
    assert "u-ref" in rows, "被引用的原话不能整行消失"
    assert rows["u-ref"]["text"] == "", "正文清掉（降级）"
    assert rows["u-ref"]["snippet"], "snippet 必须兜底保住，证据链靠它"


def test_prune_still_deletes_unreferenced(make):
    """没被引用、也没 snippet 的旧话：照旧整行删（旧语义不能修丢）。"""
    s = make()
    old = time.time() - 10 * 86400
    s.add_utterance("d", {"id": "u-free", "who": "路人", "text": "随便聊聊",
                          "snippet": "", "ts": old})
    n = s.prune_utterances("d", days=7)
    assert n == 1
    assert not s.utterances("d", days=365), "没引用的旧话该删还得删"


# ---------------- P1-4：purge 物理残留（仅 SQLite） ----------------
def test_purge_leaves_no_plaintext_in_file():
    """purge 之后，库文件字节里扫不到被删人的原话。

    修复前只 DELETE 不 VACUUM，明文留在空闲页里，"被遗忘权"是空话。
    """
    tmp = Path(tempfile.mkdtemp()) / "p.db"
    s = SqliteMemoryStore(tmp)
    secret = "身份证号430111199001011234这段必须消失"
    s.add_utterance("victim", {"id": "u1", "who": "victim", "text": secret,
                               "snippet": secret, "ts": time.time()})
    s.set_mem("core", "victim", None, "secret_key", secret)
    s.purge_designer("victim")
    s.close()
    blob = tmp.read_bytes()
    assert secret.encode("utf-8") not in blob, \
        "purge 后库文件里还能扫到明文，物理删除没落实"


# ---------------- P1-3：MemView 越权 ----------------

def test_memview_has_no_backdoor(make):
    """MemView 手里不能有 store 入口，owner/designer 只读。"""
    from artl_memory import MemView
    s = make()
    s.set_mem("core", "d", None, "基础记忆", "机密")
    v = MemView(s, owner="skill:x", designer="d")
    assert not hasattr(v, "_store"), "store 本体不能挂在视图上"
    try:
        v.owner = "core"
        raise SystemExit("owner 可写 = 越权后门")
    except AttributeError:
        pass
    assert v.get("基础记忆") is None, "skill 视图读不到 core 的基础记忆"
    v.put("k", "v")
    assert s.get_mem("skill:x", "d", None, "k") == "v", "正常读写不能修坏"


# ---------------- P1-5：remember 并发竞态 ----------------

def test_remember_concurrent_same_key(make):
    """多线程同 key 并发写不同值：最终只能有 1 条 active，且证据链不断。"""
    import threading
    mem = MemoryService(make(), designer="d")
    barrier = threading.Barrier(8)

    def worker(i):
        barrier.wait()
        mem.remember("fact", "预算", "版本%d" % i, ts=time.time())

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    active = mem._items(scope="facts", kind="fact", key="预算")
    assert len(active) == 1, "并发下同 key 只能活一条，得到 %d 条" % len(active)
    total = mem._items(scope="facts", kind="fact", key="预算", active_only=False)
    assert len(total) == 8, "旧版本必须留在证据链里（失效不是删除）"


# ---------------- P1-6：召回窗口不再静默截断 ----------------

def test_recall_beyond_old_hard_cap(make):
    """超过旧硬编码 2000 条的条目也能召回；超 1000 条去重仍生效。"""
    mem = MemoryService(make(), designer="d")
    base = time.time()
    for i in range(2100):
        mem.remember("fact", "k%d" % i, "值%d" % i, ts=base - i)
    hits = mem.recall("值2099")
    assert hits and hits[0]["value"] == "值2099", "最老那条不许永久消失"
    again = mem.remember("fact", "k2099", "值2099", ts=base)
    assert again["created"] is False, "超旧 1000 条硬截断后去重必须仍生效"


# ---------------- P1-7：短 key 加权误伤 ----------------

def test_single_char_key_does_not_outrank_real_hit(make):
    """单字 key 保命不霸榜：正文真命中必须排在前面。"""
    mem = MemoryService(make(), designer="d")
    mem.remember("note", "周", "开发时间", confidence=0.9, ts=time.time())
    mem.remember("note", "日程", "周末安排讨论会", confidence=0.9, ts=time.time())
    hits = mem.recall("周末讲什么")
    assert hits[0]["value"] == "周末安排讨论会", \
        "正文命中该第一，得到 %r" % hits[0]["value"]
    # 单字 key 的保命能力不能修丢：只有它一个候选时必须能召到
    mem2 = MemoryService(make(), designer="d")
    mem2.remember("note", "周", "开发时间", confidence=0.9, ts=time.time())
    assert mem2.recall("周讲了什么"), "单字 key 必须仍能命中"


# ---------------- P2：一致性 / 静默失败 / 边界 ----------------

def test_project_none_equals_empty(make):
    """project=None 与 '' 是同一个槽，两后端口径一致。"""
    s = make()
    s.set_mem("o", "d", None, "k", "v1")
    assert s.get_mem("o", "d", "", "k") == "v1"
    s.set_mem("o", "d", "", "k", "v2")
    assert s.get_mem("o", "d", None, "k") == "v2"


def test_read_paths_return_copies(make):
    """读路径给副本：外部改嵌套值不许污染库。"""
    s = make()
    s.add_utterance("d", {"id": "u1", "who": "甲", "text": "原话",
                          "snippet": "", "ts": time.time()})
    s.utterances("d", days=1)[0]["text"] = "篡改"
    assert s.utterances("d", days=1)[0]["text"] == "原话"
    mem = MemoryService(s, designer="d")
    mem.remember("fact", "k", {"嵌套": {"层": 1}}, ts=time.time())
    mem._items(key="k")[0]["value"]["嵌套"]["层"] = 999
    assert mem._items(key="k")[0]["value"]["嵌套"]["层"] == 1


def test_confidence_zero_is_respected(make):
    """confidence=0.0 是合法值，不许静默变 0.6。"""
    mem = MemoryService(make(), designer="d")
    item = mem.remember("fact", "k", "v", confidence=0.0, ts=time.time())
    assert item["confidence"] == 0.0
    assert mem._items(key="k")[0]["confidence"] == 0.0


def test_prune_is_idempotent(make):
    """第二次 prune 必须返回 0：没活干不许虚报。"""
    s = make()
    old = time.time() - 10 * 86400
    s.add_utterance("d", {"id": "u1", "who": "甲", "text": "原话",
                          "snippet": "原话", "ts": old})
    assert s.prune_utterances("d", days=7) == 1
    assert s.prune_utterances("d", days=7) == 0, "重复 prune 不该再计数"


def test_expire_rejects_zero_ts(make):
    """expire(id, 0) 是撤销失效的后门，必须拒收。"""
    mem = MemoryService(make(), designer="d")
    item = mem.remember("fact", "k", "v", ts=time.time())
    try:
        mem._store.expire_memory_item(item["id"], 0, designer="d")
        raise SystemExit("expire(ts=0) 没拒收 = 失效可撤销")
    except ValueError:
        pass
    try:
        mem._store.expire_memory_item(item["id"], time.time(), designer="")
        raise SystemExit("expire 不带 designer = 属主校验形同虚设")
    except ValueError:
        pass
    assert mem._items(key="k"), "条目必须仍然有效"


def test_recall_zero_limit_returns_empty(make):
    """limit<=0 返回空，不许 max(1,…) 硬塞一条。"""
    mem = MemoryService(make(), designer="d")
    mem.remember("fact", "k", "v", ts=time.time())
    assert mem.recall("v", limit=0) == []
    assert mem.recall("v", limit=-3) == []


def test_import_counts_only_new(make):
    """import_* 返回新增条数：重复导入返回 0。"""
    mem = MemoryService(make(), designer="d")
    rows = [{"text": "甲负责预算"}, {"text": "乙负责进度"}]
    assert mem.import_items(rows) == 2
    assert mem.import_items(rows) == 0, "重复导入不该虚报"
    assert mem.import_items(rows) == 0


def test_datetime_value_roundtrip(make):
    """自定义对象两套 API 同口径：都按 str 序列化，不一边 TypeError。"""
    import datetime
    s = make()
    mem = MemoryService(s, designer="d")
    dt = datetime.datetime(2026, 11, 19, 12, 0, 0)
    s.set_mem("o", "d", None, "t", dt)
    assert s.get_mem("o", "d", None, "t") == str(dt)
    item = mem.remember("fact", "t2", dt, ts=time.time())
    assert item["value"] == dt or str(item["value"]) == str(dt)


def test_recent_ops_sorted_by_ts(make):
    """乱序写入的 op 按 ts 排，两后端同口径。"""
    from artl_memory import Op
    s = make()
    base = time.time()
    s.append_op(Op(id="s1", designer="d", ts=base + 1, say="1", reply="",
                   skill="", parent=None, inputs={}, outputs={}, verdict={}, ok=True))
    s.append_op(Op(id="s0", designer="d", ts=base, say="0", reply="",
                   skill="", parent=None, inputs={}, outputs={}, verdict={}, ok=True))
    ids = [o.id for o in s.recent_ops("d", limit=10)]
    assert ids.index("s0") < ids.index("s1"), "必须按 ts 排，不是插入序"


import datetime  # noqa: E402  （test_datetime_value_roundtrip 用）


def test_purge_leaves_no_plaintext_in_file():
    """purge 之后，库文件字节里扫不到被删人的原话。

    修复前只 DELETE 不 VACUUM，明文留在空闲页里，"被遗忘权"是空话。
    第二轮自查又发现：光 VACUUM 不够，WAL 里还有删除前的整页镜像——
    所以这里连 -wal/-shm 一起扫，且关库前扫一次、关库后再扫一次。
    """
    tmp = Path(tempfile.mkdtemp()) / "p.db"
    s = SqliteMemoryStore(tmp)
    secret = "身份证号430111199001011234这段必须消失"
    s.add_utterance("victim", {"id": "u1", "who": "victim", "text": secret,
                               "snippet": secret, "ts": time.time()})
    s.set_mem("core", "victim", None, "secret_key", secret)
    s.purge_designer("victim")
    for suffix in ("", "-wal", "-shm"):  # 关库前：WAL 也该是干净的
        f = Path(str(tmp) + suffix)
        if f.exists():
            assert secret.encode("utf-8") not in f.read_bytes(), \
                f"purge 后 {f.name} 里还能扫到明文"
    s.close()
    blob = tmp.read_bytes()
    assert secret.encode("utf-8") not in blob, \
        "purge 后库文件里还能扫到明文，物理删除没落实"


def test_memview_no_one_hop_backdoor(make):
    """MemView 的 API 面零入口：无直接属性，槽位对象的一跳 __self__/
    __defaults__ 都不许摸到 store。（更深的 introspection 在 CPython 里
    防不住，那是 README 写明让走进程边界的场景。）"""
    from artl_memory import MemView
    s = make()
    v = MemView(s, owner="skill:x", designer="d")
    for attr in ("_store", "store", "_fn_get"):
        assert not hasattr(v, attr), f"直接入口没堵死: {attr}"
    for slot in type(v).__slots__:
        obj = getattr(v, slot, None)
        candidates = list(obj.values()) if isinstance(obj, dict) else [obj]
        for c in candidates:
            assert getattr(c, "__self__", None) is not s, \
                f"{slot} 的 __self__ 一跳直达 store"


def test_expire_checks_owner(make):
    """expire 带 designer 校验：拿到 id 不许作废别人的条目。"""
    s = make()
    victim = MemoryService(s, designer="甲").remember("fact", "k", "v", ts=1)
    s.expire_memory_item(victim["id"], time.time(), designer="乙")  # 乙 作废 甲 的
    assert MemoryService(s, designer="甲")._items(key="k"), "别人的条目必须还在"
    s.expire_memory_item(victim["id"], time.time(), designer="甲")  # 本人可以
    assert not MemoryService(s, designer="甲")._items(key="k")


def test_import_profile_order_insensitive(make):
    """偏好列表顺序变了不该重复插入（key 用内容哈希，不是序号）。"""
    mem = MemoryService(make(), designer="d")
    p1 = {"preferences": ["喜欢暖木", "讨厌冷光"]}
    p2 = {"preferences": ["讨厌冷光", "喜欢暖木"]}  # 同样的内容换了顺序
    assert mem.import_profile(p1) == 2
    assert mem.import_profile(p2) == 0, "顺序变化不该造成重复插入"
    assert len(mem._items(scope="experience", active_only=False)) == 2


def test_recall_window_full_warns(make):
    """召回窗口打满时必须警告，不许静默截断。"""
    import warnings as _w
    mem = MemoryService(make(), designer="d", recall_window=3)
    for i in range(5):
        mem.remember("fact", "k%d" % i, "共同词%d" % i, ts=1000 + i)
    with _w.catch_warnings(record=True) as ws:
        _w.simplefilter("always")
        mem.recall("共同词")
    assert any(issubclass(w.category, RuntimeWarning) for w in ws), \
        "窗口满了却没警告"


def test_exact_key_outranks_containing_keys(make):
    """查单字"周"：精确 key="周" 必须压过"周报""周末"这类包含它的 key。"""
    mem = MemoryService(make(), designer="d")
    mem.remember("note", "周报", "周五交周报", confidence=0.9, ts=time.time())
    mem.remember("note", "周末", "周末去看样板间", confidence=0.9, ts=time.time())
    mem.remember("note", "周", "周先生住 3 栋", confidence=0.9, ts=time.time())
    hits = mem.recall("周")
    assert hits and hits[0]["key"] == "周", \
        "精确 key 该第一，得到 %r" % [h["key"] for h in hits]


def test_remember_many_batch(make):
    """批量写入：一次落盘、照常去重、返回新增条数。"""
    mem = MemoryService(make(), designer="d")
    rows = [{"key": "k%d" % i, "value": "v%d" % i} for i in range(500)]
    t0 = time.time()
    n = mem.remember_many(rows)
    dt = time.time() - t0
    assert n == 500
    assert mem.remember_many(rows) == 0, "重放不该新增"
    assert dt < 5, "500 条批量写入不该超过 5 秒（逐条 commit 时代约 1 秒+/千条）"
    assert mem.recall("v499"), "批量写入的条目必须能召回"


def test_multiprocess_same_key_single_active():
    """多进程抢写同 key：唯一索引兜底，最终只能活一条（SQLite 专属）。

    线程锁管不到别的进程——这是评审实测打穿的洞：
    两进程各写「槽=甲/乙」后两条都活着，改口链断掉。
    """
    import subprocess
    db = Path(tempfile.mkdtemp()) / "mp.db"
    worker = (
        "import sys, time; sys.path.insert(0, %r);"
        "from artl_memory import SqliteMemoryStore, MemoryService;"
        "m = MemoryService(SqliteMemoryStore(%r), designer='d');"
        "[m.remember('fact', '槽', sys.argv[1], ts=time.time()) for _ in range(20)]"
    ) % (str(Path(__file__).resolve().parents[1]), str(db))
    ps = [subprocess.Popen([sys.executable, "-c", worker, name],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
          for name in ("甲", "乙")]
    codes = [p.wait() for p in ps]
    err = "\n".join(p.stderr.read().decode("utf-8", "replace") for p in ps)
    mem = MemoryService(SqliteMemoryStore(db), designer="d")
    alive = mem._items(key="槽")
    total = mem._items(key="槽", active_only=False)
    diag = "codes=%s alive=%d total=%d rows=%r stderr=%s" % (
        codes, len(alive), len(total),
        [(r.get("value"), r.get("valid_to")) for r in total], err[-500:])
    assert codes == [0, 0], "子进程异常退出: " + diag
    assert len(alive) == 1, "多进程下同 key 只能活一条: " + diag
    assert len(total) >= 2, "败者必须留在证据链里，不是被吞掉: " + diag


def test_multiprocess_first_open_race():
    """多进程同时首启建库：不许坏库、不许误隔离、不许丢行（CI 实测打穿过）。

    两个历史症状：并发切 WAL 把文件切坏（malformed）；后到的进程把
    先到的正在写的库改名隔离，两边各写各的（total 只剩一边的）。
    """
    import subprocess
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "race.db"
    worker = (
        "import sys, time; sys.path.insert(0, %r);"
        "from artl_memory import SqliteMemoryStore, MemoryService;"
        "m = MemoryService(SqliteMemoryStore(%r), designer='d');"
        "[m.remember('fact', '槽', sys.argv[1] + str(i), ts=time.time())"
        " for i in range(5)]"
    ) % (str(Path(__file__).resolve().parents[1]), str(db))
    ps = [subprocess.Popen([sys.executable, "-c", worker, name],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
          for name in ("甲", "乙", "丙", "丁")]
    codes = [p.wait() for p in ps]
    err = "\n".join(p.stderr.read().decode("utf-8", "replace") for p in ps)
    leftovers = [p.name for p in tmp.iterdir() if ".corrupt-" in p.name]
    mem = MemoryService(SqliteMemoryStore(db), designer="d")
    alive = mem._items(key="槽")
    total = mem._items(key="槽", active_only=False)
    diag = "codes=%s alive=%d total=%d corrupt=%s stderr=%s" % (
        codes, len(alive), len(total), leftovers, err[-500:])
    assert codes == [0] * 4, "首启竞态下有进程异常退出: " + diag
    assert not leftovers, "竞态不该触发误隔离: " + diag
    assert len(alive) == 1, "同槽只能活一条: " + diag
    assert len(total) >= 4, "四个进程的写入都得留在证据链里: " + diag


def test_schema_v1_to_v2_migration():
    """老库升级：带着同槽重复有效行的 v1 库，重开后收敛到一条并建好索引。"""
    import sqlite3 as _sq
    tmp = Path(tempfile.mkdtemp()) / "old.db"
    s = SqliteMemoryStore(tmp)
    # 手工把库降回 v1 的样子：删唯一索引、造重复有效行、版本号拨回 1
    s._db.execute("DROP INDEX IF EXISTS uniq_active_key")
    s._db.execute("DROP INDEX IF EXISTS uniq_active_keyless")
    s._db.execute("PRAGMA user_version=1")
    for i in range(3):
        s._db.execute(
            "INSERT INTO memory_items (id,designer,project,scope,kind,key,"
            "value_json,confidence,source_event,valid_from,valid_to,supersedes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?)",
            ("dup%d" % i, "d", "", "facts", "fact", "预算",
             '"三十万"', 0.6, "", 1000 + i, ""))
    s._db.commit()
    s.close()
    # 重开触发迁移
    s2 = SqliteMemoryStore(tmp)
    mem = MemoryService(s2, designer="d")
    alive = mem._items(key="预算")
    assert len(alive) == 1, "迁移后同槽只能活一条，得到 %d" % len(alive)
    assert alive[0]["valid_from"] == 1002, "留下的该是最新那条"
    idx = s2._db.execute(
        "SELECT name FROM sqlite_master WHERE name='uniq_active_key'").fetchone()
    assert idx, "唯一索引必须建好"
    v = _sq.connect(str(tmp)).execute("PRAGMA user_version").fetchone()[0]
    assert v == 3, "迁移终点必须是最新 schema 版本，得到 %d" % v
    s2.close()


def test_schema_v2_valid_to_zero_normalized():
    """v2 → v3：valid_to=0 的历史行算有效却不受唯一索引保护（双活漏洞）。

    造一个 v2 状态的库：同槽一行 valid_to=0（历史写法）加一行 NULL
    （新写法），重开后必须收敛到一条有效，且库里不再有 valid_to=0。
    """
    import sqlite3 as _sq
    tmp = Path(tempfile.mkdtemp()) / "v2.db"
    s = SqliteMemoryStore(tmp)
    MemoryService(s, designer="d").remember("fact", "预算", "四十万", ts=2.0)
    # 手工塞一行历史写法的 valid_to=0（读取侧算有效，却不在唯一索引里），
    # 再把库拨回 v2——模拟老库带着双活隐患升级
    s._db.execute(
        "INSERT INTO memory_items (id,designer,project,scope,kind,key,"
        "value_json,confidence,source_event,valid_from,valid_to,supersedes) "
        "VALUES ('zero_row','d','','facts','fact','预算','\"三十万\"',0.6,'',1,0,'')")
    s._db.execute("PRAGMA user_version=2")
    s._db.commit()
    s.close()
    # 重开触发 v2 → v3：0 行和新行先收敛，再归一
    s2 = SqliteMemoryStore(tmp)
    mem = MemoryService(s2, designer="d")
    alive = mem._items(key="预算")
    assert len(alive) == 1, "0 行和 NULL 行双活必须收敛: %r" % [
        (r["value"], r["valid_to"]) for r in
        mem._items(key="预算", active_only=False)]
    zeros = s2._db.execute(
        "SELECT COUNT(*) FROM memory_items WHERE valid_to=0").fetchone()[0]
    assert zeros == 0, "v3 之后库里不该再有 valid_to=0"
    v = _sq.connect(str(tmp)).execute("PRAGMA user_version").fetchone()[0]
    assert v == 3
    s2.close()


def test_recall_scorer_reranks(make):
    """语义插口：scorer 的高分必须能改写排序（0.3 新特性）。"""
    store = make()
    base = MemoryService(store, designer="d")
    base.remember("fact", "预算", "四十万", ts=100.0)
    base.remember("fact", "周末", "去爬山", ts=200.0)
    ids = {str(r["key"]): r["id"] for r in base._items()}
    scorer = lambda q, rows: {ids["周末"]: 0.99}
    mem = MemoryService(store, designer="d", recall_scorer=scorer,
                        semantic_weight=10.0)
    hits = mem.recall("预算")
    assert hits and hits[0]["key"] == "周末", \
        "scorer 高分必须能改排序: %r" % [r.get("key") for r in hits]
    assert hits[0]["recall_detail"]["semantic"] == 0.99
    assert hits[0]["recall_detail"]["word"] == 0.0
    plain = [str(r.get("key")) for r in base.recall("预算")]
    assert "周末" not in plain, "没装 scorer 时零重叠条目不该出现"


def test_recall_scorer_cannot_inject(make):
    """红线：scorer 只能重排，不许注入库外条目，陌生 id 直接丢弃。"""
    store = make()
    MemoryService(store, designer="d").remember("fact", "预算", "四十万",
                                                ts=100.0)
    scorer = lambda q, rows: {"mem_fake_1": 1.0, "": 0.9, "bogus": 0.8}
    mem = MemoryService(store, designer="d", recall_scorer=scorer)
    hits = mem.recall("预算")
    real_ids = {r["id"] for r in mem._items()}
    assert hits, "真实命中不该被假分数挤没"
    assert all(r["id"] in real_ids for r in hits), "scorer 注入了库外条目"
    assert all(r["recall_detail"]["semantic"] == 0.0 for r in hits), \
        "陌生 id 的分数必须被丢弃"


def test_recall_semantic_rescues_paraphrase(make):
    """换说法召回：字词零重叠的条目靠语义分 ≥ semantic_min 捞回来。"""
    store = make()
    base = MemoryService(store, designer="d")
    base.remember("fact", "预算", "客厅要花四十万", ts=100.0)
    rid = base._items()[0]["id"]
    q = "装修大概多少钱"  # 与"预算客厅要花四十万"零 bigram 重叠
    assert base.recall(q) == [], "无 scorer 时换说法就该找不到（现状钉死）"
    mem = MemoryService(store, designer="d",
                        recall_scorer=lambda q_, rows: {rid: 0.8})
    hits = mem.recall(q)
    assert hits and hits[0]["id"] == rid, "语义分过门槛必须把换说法捞回来"
    low = MemoryService(store, designer="d",
                        recall_scorer=lambda q_, rows: {rid: 0.3})
    assert low.recall(q) == [], "低于 semantic_min 不许滥捞"


def test_recall_scorer_failure_falls_back(make):
    """scorer 挂了（embedding API 宕）：警告 + 退化纯字词，召回不许跟着死。"""
    store = make()
    MemoryService(store, designer="d").remember("fact", "预算", "四十万",
                                                ts=100.0)

    def boom(q, rows):
        raise RuntimeError("embed api down")

    mem = MemoryService(store, designer="d", recall_scorer=boom)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        hits = mem.recall("预算")
    assert hits and hits[0]["key"] == "预算", "scorer 挂了召回不许跟着死"
    assert any("recall_scorer" in str(x.message) for x in w), \
        "scorer 异常必须警告而不是静默"
    assert hits[0]["recall_detail"]["semantic"] == 0.0


def test_recall_floor_latest_per_key(make):
    """槽位保底：带 key 的最新有效条目不许被 recall_window 挤掉。"""
    store = make()
    mem = MemoryService(store, designer="d", recall_window=5)
    mem.remember("fact", "预算", "四十万", ts=1.0)
    for i in range(10):
        mem.remember("fact", "", "闲聊%d号" % i, ts=100.0 + i)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # 窗口警告此处不考
        hits = mem.recall("预算")
    assert hits and hits[0]["key"] == "预算", \
        "带 key 的最新有效条目被窗口挤掉了"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        hits2 = mem.recall("闲聊0号")
    assert all(r.get("value") != "闲聊0号" for r in hits2), \
        "无 key 的老流水允许被窗口截断——这才是窗口的本职"


def test_recall_detail_formula(make):
    """recall_detail 四因子分列：word / semantic / confidence / decay 都对得上。"""
    store = make()
    m0 = MemoryService(store, designer="d")
    it = m0.remember("fact", "预算", "四十万", ts=940.0, confidence=0.8)
    rid = it["id"]
    mem = MemoryService(store, designer="d", clock=lambda: 1000.0,
                        recall_scorer=lambda q, rows: {rid: 0.5},
                        semantic_weight=4.0)
    hits = mem.recall("预算")
    d = hits[0]["recall_detail"]
    assert d["word"] == 4.0, "key 精确命中的字词分（1 基础重叠 + 3 key 加权）: %r" % d
    assert d["semantic"] == 0.5
    assert d["confidence"] == 0.8
    expect_decay = 1.0 - ((1000.0 - 940.0) / 86400.0) / 60.0
    assert abs(d["decay"] - expect_decay) < 1e-5, "decay 对不上: %r" % d


def test_export_then_purge_leaves_nothing(make):
    """查数据 + 删数据的完整闭环：先导出留副本，purge 后导出必须全空。"""
    store = make()
    mem = MemoryService(store, designer="d")
    mem.remember("fact", "预算", "四十万", ts=1.0, source="utterance:u2")
    store.add_utterance("d", {"id": "u2", "text": "四十万", "ts": 1.0})
    before = mem.export_designer()
    assert before["counts"]["memory_items"] == 1
    store.purge_designer("d")
    after = mem.export_designer()
    assert all(n == 0 for n in after["counts"].values()), \
        "purge 后导出必须全空: %r" % after["counts"]


def test_evidence_chain_walks_supersedes(make):
    """证据链溯源：顺 supersedes 从新走到旧，每环带来源和失效时间。"""
    store = make()
    mem = MemoryService(store, designer="d")
    a = mem.remember("fact", "预算", "三十万", ts=1.0, source="utterance:u1")
    b = mem.remember("fact", "预算", "三十五万", ts=2.0, source="utterance:u2")
    c = mem.remember("fact", "预算", "四十万", ts=3.0, source="utterance:u3")
    chain = mem.evidence_chain(c["id"])
    assert [x["value"] for x in chain] == ["四十万", "三十五万", "三十万"]
    assert [x["id"] for x in chain] == [c["id"], b["id"], a["id"]]
    assert all(x.get("source_event") for x in chain), "每一环都得带来源"
    assert chain[1]["valid_to"] and chain[2]["valid_to"], \
        "被替代的环带着失效时间"
    assert mem.evidence_chain("mem_nonexistent") == []


def test_export_file_roundtrip():
    """服务层写出的 JSON 文件能原样读回，UTF-8 中文不炸（仅 SQLite）。"""
    import json as _json
    store = SqliteMemoryStore(Path(tempfile.mkdtemp()) / "e.db")
    mem = MemoryService(store, designer="d")
    mem.remember("fact", "口味", "不吃香菜", ts=1.0)
    out = Path(tempfile.mkdtemp()) / "export.json"
    data = mem.export_designer(out)
    loaded = _json.loads(out.read_text(encoding="utf-8"))
    assert loaded == data
    assert loaded["tables"]["memory_items"][0]["value"] == "不吃香菜"


def main():
    import inspect
    n_pass = n_fail = 0
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        variants = _BACKENDS if "make" in inspect.signature(fn).parameters \
            else [("sqlite", None)]
        for name, make in variants:
            try:
                fn(make) if make else fn()
                n_pass += 1
                print(f"  PASS {fn.__name__} [{name}]")
            except AssertionError as exc:
                n_fail += 1
                print(f"  FAIL {fn.__name__} [{name}]: {exc}")
    print(f"\n{n_pass} passed, {n_fail} failed")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
