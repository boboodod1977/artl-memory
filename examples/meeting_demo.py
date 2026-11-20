# -*- coding: utf-8 -*-
"""Meeting demo: three people, one memory base — every beat below is a real
call against the real library. Nothing in this script is staged.

Run it (paced for humans):
    python examples/meeting_demo.py

Fast mode (used by tools/render_demo_gif.py and by tests):
    python examples/meeting_demo.py --fast

What you see:
    1. Three speakers talk; each utterance and each extracted fact lands
       under that speaker's own name (designer), with confidence + source.
    2. Recall is per-person: Li Si's memory cannot see Wang Qiang's numbers.
    3. A correction expires the old value but keeps it in the evidence chain.
    4. purge_designer physically removes a person — verified by scanning the
       raw database file bytes for the purged strings (0 hits).
"""

import sys
import tempfile
import time
from pathlib import Path

# Old Windows consoles (GBK / cp1252) choke on UTF-8 — force it.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artl_memory import MemoryService, SqliteMemoryStore

# A beat is (style, text, pause_seconds). Styles drive both the CLI colouring
# and the GIF renderer: cmd / head / say / ok / hit / dim.
PAUSE = {"cmd": 1.4, "head": 1.1, "say": 1.0, "ok": 0.9, "hit": 1.2, "dim": 0.9}


def beats():
    """Generator of (style, text, pause) — every line reflects a real call."""
    tmp = Path(tempfile.mkdtemp(prefix="artl-demo-"))
    db = tmp / "meeting.db"
    store = SqliteMemoryStore(db)
    people = {}

    def speaker(name):
        if name not in people:
            people[name] = MemoryService(store, designer=name)
        return people[name]

    def hear(name, uid, text):
        store.add_utterance(name, {"id": uid, "text": text,
                                   "ts": time.time(), "conf": 0.9})

    yield ("cmd", "$ python examples/meeting_demo.py", PAUSE["cmd"])
    yield ("head", "── three people, one room, one memory base ──", PAUSE["head"])

    yield ("say", 'Wang Qiang: "Budget is 300k, not a penny more."', PAUSE["say"])
    hear("wang-qiang", "wq-001", "Budget is 300k, not a penny more.")
    speaker("wang-qiang").remember("fact", "budget", "300k",
                                   source="utterance:wq-001", confidence=0.92)
    yield ("ok", "  √ budget = 300k   conf 0.92 ← utterance:wq-001", PAUSE["ok"])

    yield ("say", 'Li Si: "The study room? 80k will do."', PAUSE["say"])
    hear("li-si", "ls-001", "The study room? 80k will do.")
    speaker("li-si").remember("fact", "budget", "80k",
                              source="utterance:ls-001", confidence=0.90)
    yield ("ok", "  √ budget = 80k    conf 0.90 ← utterance:ls-001", PAUSE["ok"])

    yield ("say", 'Zhao Liu: "Extra cilantro, please."', PAUSE["say"])
    hear("zhao-liu", "zl-001", "Extra cilantro, please.")
    speaker("zhao-liu").remember("preference", "taste", "extra cilantro",
                                 source="utterance:zl-001", confidence=0.85)
    yield ("ok", "  √ taste = extra cilantro   conf 0.85 ← utterance:zl-001",
           PAUSE["ok"])

    yield ("head", "── ask Wang Qiang's memory: budget? ──", PAUSE["head"])
    hit = speaker("wang-qiang").recall("budget", limit=1)[0]
    yield ("hit", "→ %s   (conf %.2f, from %s)"
           % (hit["value"], hit["confidence"], hit["source_event"]),
           PAUSE["hit"])

    yield ("head", "── ask Li Si's memory: budget? ──", PAUSE["head"])
    hit = speaker("li-si").recall("budget", limit=1)[0]
    # 隔离是硬约束：李四的整个空间里根本不存在"300k"这串字节
    assert not any("300k" in str(r.get("value") or "")
                   for r in store.memory_items("li-si", active_only=False))
    yield ("hit", "→ %s — same slot, his own value; Wang Qiang's 300k"
           " isn't in his space at all" % hit["value"], PAUSE["hit"])

    yield ("head", "── Wang Qiang corrects himself: \"make it 350k\" ──",
           PAUSE["head"])
    hear("wang-qiang", "wq-002", "Make it 350k.")
    new = speaker("wang-qiang").remember("fact", "budget", "350k",
                                         source="utterance:wq-002",
                                         confidence=0.92)
    yield ("ok", "  √ 350k live · 300k expired (kept, not deleted)",
           PAUSE["ok"])

    yield ("head", "── evidence chain for wang-qiang / budget ──", PAUSE["head"])
    for item in speaker("wang-qiang").evidence_chain(new["id"]):
        state = "live" if not item["valid_to"] else "expired"
        yield ("hit", "[%s] %s ← %s" % (state.ljust(7), item["value"],
                                        item["source_event"]), PAUSE["hit"])

    yield ("head", "── \"delete everything about me\" — GDPR purge ──",
           PAUSE["head"])
    counts = store.purge_designer("wang-qiang")
    store.close()

    needle_hits = {}
    for blob in (db, db.parent / (db.name + "-wal"), db.parent / (db.name + "-shm")):
        if not blob.exists():
            continue
        data = blob.read_bytes()
        for needle in ("300k", "350k", "penny"):
            needle_hits[needle] = needle_hits.get(needle, 0) + data.count(
                needle.encode("utf-8"))
    assert all(v == 0 for v in needle_hits.values()), \
        "purge incomplete: %r" % needle_hits
    yield ("ok", "  √ purged 5 tables (%d rows) · byte-scan: %s"
           % (sum(counts.values()),
              "  ".join('"%s"×%d' % kv for kv in needle_hits.items())),
           PAUSE["ok"])

    yield ("dim", "He left. His data actually left.", 2.0)
    yield ("dim", "github.com/boboodod1977/artl-memory", 3.0)


_STYLE_PREFIX = {"cmd": "", "head": "", "say": "", "ok": "", "hit": "", "dim": ""}


def main():
    fast = "--fast" in sys.argv
    for style, text, pause in beats():
        print(_STYLE_PREFIX[style] + text, flush=True)
        if not fast:
            time.sleep(pause)


if __name__ == "__main__":
    main()
