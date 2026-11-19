# artl-memory

**The multi-person memory layer for agents.**
逐句原话 · 按人隔离 · 证据链 · 把握度。

> It remembers everyone in the room — separately.
> 我们不承诺理解，我们承诺不忘。

artl-memory 是 ARTL 家族（ARTLESS 助理、ARTLE 系列）的记忆层，从生产环境
的自研助理中抽出。它不做"总结你的画像然后假装懂你"——它保存**谁、在什么时间、
说了什么原话**，每条记忆带着来源和把握度；记错了就标记失效，证据链留在原地。

```bash
pip install .        # 本地源码安装（PyPI 包尚未发布）
```

## 为什么是 multi-person

市面上的 agent 记忆方案（Mem0、Letta、Zep……）都是**单用户**模型：一个用户，
一份画像。artl-memory 从第一天就是多人模型：

- **按人隔离**：王强的旧话不会进李四的口；技能/插件只见自己的记忆——
  `MemView` 上不挂 store 本体，正常 API 面没有别人的入口
  （同进程 introspection 挖得回去，要防恶意代码请上进程边界）
- **逐句原话**：先存原文，判断当场重算；改写原文在这条链路里不存在
- **证据链**：每条记忆带 `source_event`（它是从哪句话来的）和 `confidence`；
  改口了，旧值标记失效并挂在 `supersedes` 上——**失效不是删除**
- **到期降级**：原话留 N 天，没被引用的整行清，被引用的清正文留摘要
- **被遗忘权**：`store.purge_designer("王强")` 把这个人的五类数据真删干净，
  返回各类删了多少条——是硬删除，不是标记

## 上手（30 秒）

```python
from artl_memory import SqliteMemoryStore, MemoryService, MemView

store = SqliteMemoryStore("memory.db")
mem = MemoryService(store, designer="heima")

mem.remember("preference", "王强:预算", "三十万",
             source="utterance:wq-001", confidence=0.9)
mem.recall("王强的预算")        # 相关度 × 把握度 × 时间衰减，每个因子可解释

view = MemView(store, owner="skill:render", designer="heima")
view.put("风格", "奶油风")       # 技能的记忆，别人的进不来
```

跑通多人示例：

```bash
python examples/two_people.py
```

## 零依赖，两个后端

- **零硬依赖**：只用标准库（`sqlite3` 内置）。记忆层不该拖着一堆包进门。
- `InMemoryStore`：测试与试用（非线程安全）；`SqliteMemoryStore`：WAL、
  单连接、连接被关自动重开、坏文件自动隔离、schema 带版本号。
- 同一套一致性测试跑两个后端：`python tests/test_conformance.py`（也兼容 pytest）
- 对抗性测试管"没说也不许炸"：`python tests/test_adversarial.py`

## 边界（写在明处）

- 值经 JSON 序列化存储：`tuple` 读出会变 `list`，自定义对象请自己序列化。
- `recall` 是 O(n) 全量打分——为可解释性刻意不做索引。扫描上限由
  `MemoryService(recall_window=...)` 控制，默认 10000 条（对齐"万级以下"
  的承诺）；超过这个规模请显式调大，或在自己的后端里建索引。

## 明确不做

- **不做向量检索**。bigram 召回是刻意的：你随时能看清"它为什么想起这条"。
  需要向量，自己在 recall 之上接。
- **不做云端同步、不做多机**。数据在你的文件里，这是特性不是缺陷。
- **不调用任何大模型**。记忆层不知道 LLM 存在——脑子随便换，记忆不动。

## English

artl-memory is a local-first memory layer for agents that need to remember
**multiple people, separately**. Verbatim utterances, per-person isolation,
evidence chains with provenance and confidence, and expiry-instead-of-delete.
Zero dependencies, SQLite or in-memory backends, no LLM calls — bring any brain.

## License

Apache-2.0 · ARTL family
