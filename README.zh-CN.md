# artl-memory

**The multi-person memory layer for agents.**
逐句原话 · 按人隔离 · 证据链 · 把握度。

> It remembers everyone in the room — separately.
> 我们不承诺理解，我们承诺不忘。

[English](README.md)

artl-memory 是 ARTL 家族（ARTLESS 助理、ARTLE 系列）的记忆层，从生产环境
的自研助理中抽出。它不做"总结你的画像然后假装懂你"——它保存**谁、在什么时间、
说了什么原话**，每条记忆带着来源和把握度；记错了就标记失效，证据链留在原地。

> **边界：它是账本，不是耳朵。** 库里没有音频、没有 ASR、没有声纹——
> "这句话是谁说的"由上层管线（声纹 / 说话人分离 / 人工标注）判断完，
> 把人名交给它记账。零依赖就是这么来的：不接声纹也能用，接了即插即用。

```bash
pip install .        # 本地源码安装（PyPI 包尚未发布）
```

## 该选哪个记忆层？

来看记忆库的人，参照物几乎都是 Mem0（6 万多星），绕不开，那就直接摆开说。
这四个工具回答的是**不同的问题**——这张表帮你选型，不是打榜。

| | **artl-memory** | **Mem0** | **memoripy v4** | **MCP memory（官方）** |
|---|---|---|---|---|
| 写入要不要调 LLM | **不用**，零模型调用 | 要，每次 `add()` 过抽取模型 | 非必需，确定性抽取器，可自接模型 | 不用，agent 自己决定记什么 |
| 存的是 | **逐句原话** + 派生事实 | LLM 抽取的摘要 | 证据 + 派生记忆，带版本 | 实体/关系/观察三元组 |
| 记忆能追到来源吗 | 能，每条带 `source_event` + 把握度 | 部分，抽取会改写措辞 | 能，citations + receipts | 不能 |
| 多人隔离 | **一等公民**：按人分账，技能拿受限 `MemView` | 按 `user_id` 分区 | 按 `user_id` 分区 | 没有，全库共享一张图 |
| 删除 | **物理硬删**：secure_delete + VACUUM + WAL 截断，字节级扫描验证 | API 删除，向量库语义 | forget 工具 | JSONL 上的删除工具 |
| 完全离线可用 | 是 | 否（LLM + 向量库调用） | 是 | 是 |
| 语义（向量）检索 | **没有**，bigram 可解释打分，刻意的 | 有 | 可选嵌入 | 没有，子串匹配 |
| 依赖 | **0**（标准库 `sqlite3`） | LLM 客户端 + 向量库 + embedding | 0 必需（v4） | Node.js 运行时 |

公道话：

- **选 Mem0**：要"说完话自动长出画像"、要语义检索、要成熟的框架集成
  （LangChain/CrewAI/AutoGen）和公开 benchmark 数字——这些都是真价值。
  代价它自己社区也记着账：一次 10134 条条目的审计发现 97.8% 是垃圾，
  包括幻觉出来的用户画像会被反复再抽取、跨会话放大
  （[mem0#4573](https://github.com/mem0ai/mem0/issues/4573)）；HaluMem
  基准发现抽取阶段的幻觉在 LLM 系记忆系统里是系统性的
  （[arXiv:2511.03506](https://arxiv.org/abs/2511.03506)）。如果记忆措辞
  不需要"可证明就是某人原话"，这个代价对你可能完全划算。
- **选 memoripy**：认同证据优先理念、但场景是**单用户**，且想要开箱即用的
  官方 MCP server。
- **选官方 MCP memory**：一台机器一个 agent 的极简记事本。它自称参考实现，
  也确实就是参考实现。
- **选 artl-memory**：当问题是"**多个人对一个 agent 说话——会议室、家庭、
  调解现场——而且记录日后要站得住**"：谁说了哪句原话、转写把握度多少、
  改口之前系统信的是什么、王强要求删除时能不能真删到字节级。
  需要聪明画像就别选我们——这个 README 下面还会再劝退你一次。

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
  `MemoryService(recall_window=...)` 控制，默认 10000 条；窗口打满时
  发 `RuntimeWarning`，最老的条目可能没参与打分，不会静默截断。
  批量导入请用 `remember_many(...)`（整批一次落盘，比逐条快约两个数量级），
  并确认窗口大于你的条目总量。
- **recall 只按字词重叠打分，不懂释义**。换说法、中英混写会找不到：
  查询 `who pays thirty万 for the living room` 召不回中文条目「三十万」。
  需要语义检索，在 recall 之上自己接向量层。
- 时间衰减有 0.4 的下限：很旧的高把握度条目会一直参与排序，只是权重打折。
  想让旧记忆彻底退出排序，请 `forget` 它（失效，不删除）。
- 空查询返回空列表——"你想找什么"没有答案时，不假装理解。
- 锁的边界：`MemoryService.remember` 的线程安全由服务层锁保证；
  `InMemoryStore` 本身不是线程安全的，请只经服务层使用。
  多进程写同一个 SQLite 文件由数据库层的唯一索引兜底
  （同槽位同 key 全库只允许一条有效），冲突方自动重读收敛。

## 明确不做

- **不做向量检索**。bigram 召回是刻意的：你随时能看清"它为什么想起这条"。
  需要向量，自己在 recall 之上接。
- **不做云端同步、不做多机**。数据在你的文件里，这是特性不是缺陷。
- **不调用任何大模型**。记忆层不知道 LLM 存在——脑子随便换，记忆不动。

## English

See [README.md](README.md).

## License

Apache-2.0 · ARTL family
