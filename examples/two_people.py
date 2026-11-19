# -*- coding: utf-8 -*-
"""最小示例：两个人轮流说话，记忆互不串味。

跑法：
    python examples/two_people.py

你会看到：
    1. 王强和李四在同一屋檐下说了各自的偏好
    2. 王强的旧话不会进李四的口——按人隔离是硬约束
    3. 每条记忆带来源和把握度，失效是标记不是删除（证据链）
"""

import sys
import tempfile
import time
from pathlib import Path

# Windows 老控制台（GBK / cp1252）打印中文会炸，把 stdout 切到 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artl_memory import MemoryService, SqliteMemoryStore

db = Path(tempfile.mkdtemp()) / "demo.db"
store = SqliteMemoryStore(db)

# ---- 一屋两人 -------------------------------------------------------------
mem = MemoryService(store, designer="heima")   # heima 是这间屋子的主人

# 王强说的话，证据指向那句原话
mem.remember("preference", "王强:预算", "三十万",
             source="utterance:wq-001", confidence=0.9)
# 李四也说了预算——同一个 key 槽位，不同的人，不串
mem.remember("preference", "李四:预算", "十五万",
             source="utterance:ls-001", confidence=0.8)

# ---- 召回：带着把握度回来 ---------------------------------------------------
hits = mem.recall("王强的预算")
for h in hits[:2]:
    print(f"召回: {h['key']} = {h['value']}  (把握度 {h['confidence']}, 来源 {h['source_event']})")

# ---- 改口：三十万 → 三十五万，旧的失效但留在证据链里 --------------------------
old = mem.recall("王强", limit=1)[0]
mem.remember("preference", "王强:预算", "三十五万",
             source="utterance:wq-002", confidence=0.9)
new = mem.recall("王强的预算", limit=1)[0]
print(f"\n改口后: {new['value']}  (替代了 {new['supersedes']})")

history = store.memory_items("heima", key="王强:预算", active_only=False)
print(f"证据链: {len(history)} 条（旧的没删，只是标记失效）")
for h in history:
    state = "有效" if not h["valid_to"] else "已失效"
    print(f"  [{state}] {h['value']}  ← {h['source_event']}")

store.close()
print("\n两个人、两个价，各是各的——这就是 multi-person memory。")
