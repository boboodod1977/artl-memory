# -*- coding: utf-8 -*-
"""SQLite 后端。五张表：ops / utterances / memory / memory_items / trace。

设计要点：
    - 单连接 + 可重入锁；连接被关过会自动重开（后台线程拿到旧 store 不会鞭尸）
    - 文件损坏时隔离现场重开，不让整个应用起不来
    - WAL 模式，Windows 友好
    - 逐句原话到期降级：没被引用的整行删，被引用的清正文留 snippet
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import warnings
from pathlib import Path
from typing import Any

from .types import Op

SCHEMA = """
CREATE TABLE IF NOT EXISTS ops (
    id       TEXT PRIMARY KEY,
    designer TEXT NOT NULL,
    ts       REAL NOT NULL,
    say      TEXT,
    reply    TEXT,
    skill    TEXT,
    parent   TEXT,
    inputs   TEXT,
    outputs  TEXT,
    verdict  TEXT,
    ok       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ops_designer ON ops(designer, ts);

CREATE TABLE IF NOT EXISTS utterances (
    id       TEXT PRIMARY KEY,
    designer TEXT NOT NULL,
    session  TEXT,
    who      TEXT,
    text     TEXT,
    snippet  TEXT,
    ts       REAL NOT NULL,
    conf     REAL DEFAULT 0,
    dur      REAL DEFAULT 0,
    kind     TEXT DEFAULT 'say'
);
CREATE INDEX IF NOT EXISTS idx_utt ON utterances(designer, ts);

CREATE TABLE IF NOT EXISTS memory (
    owner      TEXT NOT NULL,
    designer   TEXT NOT NULL,
    project    TEXT NOT NULL DEFAULT '',
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    ts         REAL NOT NULL,
    PRIMARY KEY (owner, designer, project, key)
);

CREATE TABLE IF NOT EXISTS trace (
    id       TEXT PRIMARY KEY,
    designer TEXT NOT NULL,
    op_id    TEXT,
    kind     TEXT NOT NULL DEFAULT 'turn',
    ts       REAL NOT NULL,
    steps    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trace ON trace(designer, ts);

CREATE TABLE IF NOT EXISTS memory_items (
    id          TEXT PRIMARY KEY,
    designer    TEXT NOT NULL,
    project     TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT 'facts',
    kind        TEXT NOT NULL DEFAULT 'fact',
    key         TEXT NOT NULL DEFAULT '',
    value_json  TEXT NOT NULL DEFAULT '{}',
    confidence  REAL NOT NULL DEFAULT 0.6,
    source_event TEXT,
    valid_from  REAL NOT NULL DEFAULT 0,
    valid_to    REAL,
    supersedes  TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_scope
    ON memory_items(designer, project, scope, kind, key);
"""


def _key(project) -> str:
    return "" if project is None else str(project)


class SqliteMemoryStore:
    """记忆层的 SQLite 实现。ARTL 家族的应用可以继承它、往上加自己的表。"""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._raw = None
        # 坏文件被隔离过会置 True 并发警告：调用方能区分"连着正常空库"
        # 和"数据被隔离、现在连的是重建的空库"，不再是静默失败。
        self.quarantined = False
        try:
            self._db = self._open()
        except sqlite3.DatabaseError:
            # 文件坏了也不能让整个应用起不来：留个现场，重新开一个
            self._quarantine()
            self.quarantined = True
            warnings.warn("数据库文件损坏已隔离重建（原文件保留为 .corrupt-*）：%s"
                          % self.path, RuntimeWarning, stacklevel=2)
            self._db = self._open()

    @property
    def _db(self):
        """连接被关过就重开；后台线程拿到旧 store 也不至于鞭尸。"""
        with self._lock:
            if self._closed or self._raw is None:
                self._raw = self._open()
                self._closed = False
            return self._raw

    @_db.setter
    def _db(self, conn) -> None:
        self._raw = conn
        self._closed = False

    @property
    def closed(self) -> bool:
        return bool(self._closed) or self._raw is None

    def _open(self):
        db = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            # schema 版本号：以后加表/改结构靠它做迁移判断，不靠猜
            if db.execute("PRAGMA user_version").fetchone()[0] == 0:
                db.execute("PRAGMA user_version=1")
            db.commit()
        except Exception:
            # 必须先关掉：Windows 下被占用的文件重命名会失败，
            # 那样隔离就成了一句空话，下一次 open 还是读到同一个坏文件。
            try:
                db.close()
            except Exception:
                pass
            raise
        return db

    def _quarantine(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            src = Path(self.path + suffix)
            if not src.exists():
                continue
            try:
                src.rename(Path(str(src) + ".corrupt-" + stamp))
            except OSError:
                try:
                    src.unlink()
                    warnings.warn("坏文件隔离失败已直接删除：%s" % src,
                                  RuntimeWarning, stacklevel=2)
                except OSError:
                    # 关库失败，交给系统回收；但不能无痕
                    warnings.warn("坏文件隔离/删除均失败，交给系统回收：%s" % src,
                                  RuntimeWarning, stacklevel=2)

    # ---------------- 隐私：把一个人整个抹掉 ----------------
    def purge_designer(self, designer: str) -> dict:
        """删除这个人的全部数据（ops/utterances/memory/memory_items/trace）。

        返回各表删掉的行数。这是"被遗忘权"的硬实现：不是标记，是真删。
        secure_delete 让删除时覆写原页，VACUUM 重建库文件——否则已删的
        明文会一直躺在文件空闲页里，"删除"就成了空话。
        """
        out = {}
        with self._lock:
            self._db.execute("PRAGMA secure_delete=ON")
            for table in ("ops", "utterances", "memory", "memory_items", "trace"):
                cur = self._db.execute(f"DELETE FROM {table} WHERE designer=?", (designer,))
                out[table] = cur.rowcount or 0
            self._db.commit()
            # VACUUM 不能在事务里跑；此时 commit 已完成，安全。
            # 光 VACUUM 不够：WAL 文件里还留着删除前的整页镜像，
            # 必须 checkpoint(TRUNCATE) 把 WAL 截成零字节，物理删除才算闭环。
            self._db.execute("VACUUM")
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return out

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            db, self._raw = self._raw, None
            if db is not None:
                db.close()

    # ---------------- 决策轨迹（append-only，只增不改） ----------------
    def append_trace(self, designer: str, row: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO trace (id,designer,op_id,kind,ts,steps) "
                "VALUES (?,?,?,?,?,?)",
                (row.get("id"), designer, row.get("op_id") or "",
                 row.get("kind") or "turn", float(row.get("ts") or 0),
                 json.dumps(row.get("steps") or [], ensure_ascii=False, default=str)),
            )
            self._db.commit()

    def traces(self, designer: str, limit: int = 50, before: float = 0.0) -> list:
        with self._lock:
            if before:
                rows = self._db.execute(
                    "SELECT * FROM trace WHERE designer=? AND ts < ? "
                    "ORDER BY ts DESC LIMIT ?", (designer, before, limit)).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM trace WHERE designer=? ORDER BY ts DESC LIMIT ?",
                    (designer, limit)).fetchall()
        return [{"id": r["id"], "designer": r["designer"], "op_id": r["op_id"] or "",
                 "kind": r["kind"], "ts": r["ts"],
                 "steps": json.loads(r["steps"] or "[]")} for r in rows]

    # ---------------- 统一记忆条目（证据链） ----------------
    def add_memory_item(self, item: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO memory_items "
                "(id,designer,project,scope,kind,key,value_json,confidence,"
                " source_event,valid_from,valid_to,supersedes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (item.get("id"), item.get("designer") or "",
                 item.get("project") or "", item.get("scope") or "facts",
                 item.get("kind") or "fact", item.get("key") or "",
                 json.dumps(item.get("value"), ensure_ascii=False, default=str),
                 (0.6 if item.get("confidence") is None
                  else float(item.get("confidence"))),
                 item.get("source_event") or "", float(item.get("valid_from") or 0),
                 item.get("valid_to"), item.get("supersedes") or ""),
            )
            self._db.commit()

    def memory_items(self, designer: str, project: str = "", scope: str = "",
                     kind: str = "", key: str = "", active_only: bool = True,
                     limit: int = 1000) -> list:
        sql = "SELECT * FROM memory_items WHERE designer=? AND project=?"
        args: list[Any] = [designer, project]
        if scope:
            sql += " AND scope=?"
            args.append(scope)
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if key:
            sql += " AND key=?"
            args.append(key)
        if active_only:
            sql += " AND (valid_to IS NULL OR valid_to=0)"
        sql += " ORDER BY valid_from DESC LIMIT ?"
        # 调用方给多少就是多少，不在这里偷偷设天花板——静默截断
        # 意味着"最老的那条永远想不起来"，比慢更可怕。
        args.append(max(1, int(limit)))
        with self._lock:
            rows = self._db.execute(sql, tuple(args)).fetchall()
        out = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            try:
                d["value"] = json.loads(d.pop("value_json") or "{}")
            except Exception:
                warnings.warn("memory_items.value_json 解析失败，按空值返回: %r"
                              % (d.get("id"),), RuntimeWarning, stacklevel=2)
                d["value"] = {}
            out.append(d)
        return out

    def expire_memory_item(self, item_id: str, ts: float, designer: str | None = None) -> None:
        ts = float(ts)
        if ts <= 0:
            # valid_to=0 在读取侧被当作"有效"（历史兼容），放任 ts<=0 写进去
            # 等于给"撤销失效"留后门。失效是证据链事件，必须带真实时间。
            raise ValueError("expire 的时间戳必须 > 0")
        with self._lock:
            if designer is not None:
                # 属主校验：拿到 id 不许作废别人的条目
                self._db.execute(
                    "UPDATE memory_items SET valid_to=? WHERE id=? AND designer=?",
                    (ts, item_id, designer))
            else:
                self._db.execute("UPDATE memory_items SET valid_to=? WHERE id=?",
                                 (ts, item_id))
            self._db.commit()

    # ---------------- op 链 ----------------
    def append_op(self, op: Op) -> None:
        with self._lock:
            if op.parent is None:
                row = self._db.execute(
                    "SELECT id FROM ops WHERE designer=? ORDER BY ts DESC LIMIT 1",
                    (op.designer,),
                ).fetchone()
                if row:
                    op.parent = row["id"]
            self._db.execute(
                "INSERT OR REPLACE INTO ops "
                "(id,designer,ts,say,reply,skill,parent,inputs,outputs,verdict,ok) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (op.id, op.designer, op.ts, op.say, op.reply, op.skill, op.parent,
                 json.dumps(op.inputs, ensure_ascii=False),
                 json.dumps(op.outputs, ensure_ascii=False, default=str),
                 json.dumps(op.verdict, ensure_ascii=False),
                 1 if op.ok else 0),
            )
            self._db.commit()

    def _row_to_op(self, row) -> Op:
        return Op(
            id=row["id"], designer=row["designer"], ts=row["ts"],
            say=row["say"] or "", reply=row["reply"] or "",
            skill=row["skill"], parent=row["parent"],
            inputs=json.loads(row["inputs"] or "{}"),
            outputs=json.loads(row["outputs"] or "{}"),
            verdict=json.loads(row["verdict"] or "{}"),
            ok=bool(row["ok"]),
        )

    def last_op(self, designer: str) -> Op | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM ops WHERE designer=? ORDER BY ts DESC LIMIT 1", (designer,)
            ).fetchone()
        return self._row_to_op(row) if row else None

    def recent_ops(self, designer: str, limit: int = 20, before: float = 0.0) -> list[Op]:
        with self._lock:
            if before:
                rows = self._db.execute(
                    "SELECT * FROM ops WHERE designer=? AND ts < ? "
                    "ORDER BY ts DESC LIMIT ?", (designer, before, limit)).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM ops WHERE designer=? ORDER BY ts DESC LIMIT ?",
                    (designer, limit)).fetchall()
        return [self._row_to_op(r) for r in reversed(rows)]

    def count_ops(self, designer: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM ops WHERE designer=?", (designer,)
            ).fetchone()
        return int(row["n"]) if row else 0

    def clear_ops(self, designer: str) -> int:
        with self._lock:
            cur = self._db.execute("DELETE FROM ops WHERE designer=?", (designer,))
            self._db.commit()
        return cur.rowcount or 0

    def clear_mem(self, owner: str, designer: str) -> int:
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM memory WHERE owner=? AND designer=?", (owner, designer))
            self._db.commit()
        return cur.rowcount or 0

    # ---------------- 逐句原话（到期降级） ----------------
    def add_utterance(self, designer: str, row: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO utterances"
                "(id,designer,session,who,text,snippet,ts,conf,dur,kind) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (row.get("id"), designer, row.get("session") or "", row.get("who") or "",
                 row.get("text") or "", row.get("snippet") or "", float(row.get("ts") or 0),
                 float(row.get("conf") or 0), float(row.get("dur") or 0),
                 row.get("kind") or "say"))
            self._db.commit()

    def utterances(self, designer: str, days: int = 7, limit: int = 200) -> list:
        cutoff = time.time() - days * 86400
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM utterances WHERE designer=? AND ts>=? "
                "ORDER BY ts DESC LIMIT ?", (designer, cutoff, int(limit))).fetchall()
        out = [dict(r) for r in rows]
        out.reverse()
        return out

    def count_utterances(self, designer: str, days: int | None = None) -> int:
        with self._lock:
            if days is None:
                row = self._db.execute("SELECT COUNT(*) FROM utterances WHERE designer=?",
                                       (designer,)).fetchone()
            else:
                cutoff = time.time() - days * 86400
                row = self._db.execute(
                    "SELECT COUNT(*) FROM utterances WHERE designer=? AND ts>=?",
                    (designer, cutoff)).fetchone()
        return int(row[0] if row else 0)

    def prune_utterances(self, designer: str, days: int = 7) -> int:
        """超过 N 天：没引用过的整行删，有引用的正文清空（snippet 留着）。

        "有引用"的判定：snippet 非空（写入时标记），或被
        memory_items.source_event 点名（证据链）。被引用的行先用正文
        兜底 snippet——不清这一步，DELETE 会把证据链整行端走。
        """
        cutoff = time.time() - days * 86400
        _ref = ("EXISTS (SELECT 1 FROM memory_items mi "
                "WHERE mi.designer=utterances.designer AND mi.source_event != '' "
                "AND instr(mi.source_event, utterances.id) > 0)")
        with self._lock:
            # 被记忆条目引用但 snippet 为空的：先用正文兜底 snippet，
            # 让下面的 DELETE 自然绕过它，证据链不断
            self._db.execute(
                "UPDATE utterances SET snippet=substr(text,1,40) "
                "WHERE designer=? AND ts<? AND (snippet IS NULL OR snippet='') "
                "AND " + _ref, (designer, cutoff))
            n1 = self._db.execute(
                "DELETE FROM utterances WHERE designer=? AND ts<? "
                "AND (snippet IS NULL OR snippet='')", (designer, cutoff)).rowcount or 0
            n2 = self._db.execute(
                "UPDATE utterances SET text='' WHERE designer=? AND ts<? "
                "AND text!=''",  # 已清过的不再重复计数：prune 幂等
                (designer, cutoff)).rowcount or 0
            self._db.commit()
        return int(n1) + int(n2)

    # ---------------- 记忆（按 owner/designer/project 隔离） ----------------
    def clear_all_mem(self, designer: str) -> int:
        """这个人的所有记忆，不分 owner。"""
        with self._lock:
            cur = self._db.execute("DELETE FROM memory WHERE designer = ?", (designer,))
            self._db.commit()
            return cur.rowcount or 0

    def set_mem(self, owner, designer, project, key, value, confidence: float = 1.0) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO memory (owner,designer,project,key,value,confidence,ts) "
                "VALUES (?,?,?,?,?,?,?)",
                (owner, designer, _key(project), key,
                 json.dumps(value, ensure_ascii=False, default=str), confidence, time.time()),
            )
            self._db.commit()

    def get_mem(self, owner, designer, project, key, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM memory WHERE owner=? AND designer=? AND project=? AND key=?",
                (owner, designer, _key(project), key),
            ).fetchone()
        return default if row is None else json.loads(row["value"])

    def all_mem(self, owner, designer, project=None) -> dict:
        return {k: v["value"] for k, v in self.snapshot_mem(owner, designer, project).items()}

    def snapshot_mem(self, owner, designer, project=None) -> dict:
        with self._lock:
            rows = self._db.execute(
                "SELECT key,value,confidence,ts FROM memory WHERE owner=? AND designer=? AND project=?",
                (owner, designer, _key(project)),
            ).fetchall()
        return {
            r["key"]: {"value": json.loads(r["value"]), "confidence": r["confidence"], "ts": r["ts"]}
            for r in rows
        }
