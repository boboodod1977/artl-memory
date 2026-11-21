# Changelog

本项目遵循语义化版本。0.x 阶段： minor 可能含不兼容变更，patch 只修 bug。

## [0.5.0] - 2026-11-21

MCP 挂件：Claude Desktop / Cursor 一段配置接入。

### 新增

- **`artl-memory[mcp]` extra + `artl-memory-mcp` 命令**：八个工具
  （`remember` / `recall` / `recall_all` / `list_people` / `evidence_chain`
  / `forget` / `purge_person` / `export_person`），默认 stdio 传输。
  核心依旧零依赖——`mcp` 包只随挂件安装。
- **`store.designers()`**：五张表的花名册，"你都记得谁"成为一等接口。
  Port、内存、SQLite 三处同步实现，双后端一致性测试钉死。
- `recall_all` 跨人召回：每条结果带 `person` 字段，按公开因子还原总分排序。

### 测试

- 总计 116 项：回归 68 + 一致性 28 + 对抗 10 + MCP 10。
- MCP 协议层测试含真 stdio 握手（起 server 子进程 → initialize →
  调用 remember/recall），不是 mock。
- CI 新增两道门：演示脚本 `meeting_demo.py --fast` 必跑（README 的 GIF
  由它实录）；MCP 测试装上 extra 后随矩阵运行。

## [0.4.1] - 2026-11-20

发布前第三轮外部评审（88/100）驱动的合规修复。

### 修复

- **schema v3**：`valid_to=0` 的历史行读取侧算有效、却不在唯一索引的保护
  范围里，同槽可能双活。迁移时先按"0 也算有效"收敛再把 0 归一成 NULL；
  新库直接建 v3。正常使用碰不到，但评审说得对——有效态只该有一种写法。

### 合规

- LICENSE 版权行替换模板占位符（Copyright 2026 ARTL family）。
- 中文 README 去掉私有产品名；修正"Mem0/Letta/Zep 都是单用户"的过界表述
  （它们能按 user_id 分区，做不到的是同一段对话里多人原话分开记账）。
- sdist 补 MANIFEST.in：中文 README、CHANGELOG、examples、benchmarks、tests
  全部进包。
- pyproject 补 Python 3.11/3.12/3.13 分类器；仓库加 GitHub topics。
- 仓库级 git 邮箱切换为 GitHub noreply（历史提交里的邮箱不回溯改写）。

## [0.4.0] - 2026-11-19

GROK 路线图的第三步：把"可信"从口号变成可交付物。
103 项测试（回归 67 + 一致性 26 + 对抗 10）双后端通过。

### 新增

- **GDPR 数据可携带**：`store.export_designer(designer)` 把这个人的五张表
  （ops / utterances / memory / memory_items / trace）全量打包成可核验的
  dict；`MemoryService.export_designer(path)` 顺手写成 UTF-8 JSON 文件。
  查数据（export）+ 删数据（purge_designer 物理硬删）两项权利成对闭环，
  回归测试钉死"purge 后导出必须全空"。
- **证据链溯源**：`MemoryService.evidence_chain(item_id)` 顺 supersedes
  从新走到旧——"它现在这么认为、之前那么认为、依据分别是哪句原话"
  一条链说清，每环带 source_event 和 valid_to。配合 0.3 的 recall_detail，
  "为什么召回这条"全因子可查。
- **公开评测** `benchmarks/recall_benchmark.py`：3 人物、12 写入、10 查询
  全部写死在脚本里，字符串包含判定（裁判不是选手），字面命中 / 改口时效 /
  按人隔离 / 换说法单列，违规串供计数。Mem0 对照 adapter 内置
  （`--with-mem0`，默认配置），公平性约定写在 benchmarks/README.md。

## [0.3.0] - 2026-11-19

GROK 四步路线图的第二步：语义做成可选插口，核心继续零依赖。
96 项测试（回归 62 + 一致性 24 + 对抗 10）双后端通过。

### 新增

- **可选语义召回插口**：`MemoryService(recall_scorer=...)`，协议为
  `callable(query, rows) -> {id: 0..1}`。红线：scorer 只能给库内已有候选
  改排序分，陌生 id 直接丢弃——重排可以，注入不行；scorer 异常时警告并
  退化为纯字词打分，召回不跟着死。合并公式公开：
  `相关度 = 字词分 + semantic_weight × 语义分`（`semantic_weight` 默认 3.0），
  字词零重叠的条目需语义分 ≥ `semantic_min`（默认 0.5）才进结果。
- **`artl_memory.contrib.EmbeddingScorer`**：把任何批量 embedding 函数
  （本地小模型或任意 API）包装成 recall_scorer，按条目 id 缓存向量。
  `fastembed_scorer()` 一键接本地 bge-small-zh（`pip install
  artl-memory[semantic]`，不装就是纯零依赖）。
- **`recall_detail`**：每条召回结果带 `word / semantic / confidence /
  decay` 四个因子分列，"为什么想起这条"随时可查。
- **召回槽位保底**：`recall_window` 截断只作用于无 key 的流水；每个非空
  key 的最新有效条目永远参与打分（后端新增 `per_key_latest` 查询，
  双后端一致性已测试）。

### 修复

- **多进程首启竞态**（CI 实测两种死法）：并发切换 WAL 把库切坏
  （malformed）；后到的进程初始化撞异常就把先到的正在写的库误隔离，
  两边各写各的丢记忆。现在初始化对锁竞争和结构错误都退避重试，
  已是 WAL 不重复切换，重试耗尽才允许隔离；隔离文件名带进程号。

### 修正

- pyproject 的 Homepage 指向真实仓库地址。

## [0.2.0] - 2026-11-19

两轮独立外部测评（5.5/10 → 65/100 → 75/100）+ 一轮自我苛刻测评驱动的修复与改进。
全部 81 项测试（回归 49 + 一致性 22 + 对抗 10）双后端通过，另有 11 项攻击探针。

### 不兼容变更

- `recall("")` 空查询改为返回空列表（原行为：返回按把握度排序的全量）。
- `remember` / `import_*` 返回的条目新增 `created` 标记（True=新写入，False=去重命中）；
  `import_*` 返回值语义从"处理数"改为"新增条数"。
- `expire_memory_item` 拒收 `ts <= 0`（ValueError），且 `designer` 改为必填
  做属主校验——拿到 id 作废不了别人的条目。
- `import_profile` 的 key 从列表序号改为内容哈希——重复导入同一份画像（即使顺序
  变化）不再产生重复条目；旧数据如需去重请 purge 后重新导入。
- SQLite schema 升至 v2（`user_version=2`），老库打开时自动迁移
  （同槽位重复有效条目收敛到最新一条后建立唯一索引）。

### 修复（按外部测评编号）

- **P0-1** `remember` 去重不限定 key，两人记同值时第二人被吞（service.py）
- **P0-2** `prune_utterances` 不检查 `memory_items.source_event` 引用，
  证据链整行被删（双后端）
- **P1-3** `MemView` 暴露 store 本体与可写 owner/designer → 闭包封装 + 只读属性
  + `__slots__`（API 面零入口；同进程 introspection 的边界已写入 README）
- **P1-4** `purge_designer` 物理残留：`secure_delete` + `VACUUM` +
  `wal_checkpoint(TRUNCATE)`，回归测试字节级扫描主库与 WAL
- **P1-5** `remember` check-then-act 加服务层锁（线程）
- **P1-6** recall/_items 硬编码 2000/1000 截断 → `recall_window` 参数化
  （默认 10000），窗口打满发 `RuntimeWarning`，不再静默
- **P1-7** 单字 key 加权霸榜 → 0.5 小分保命；精确相等的 key 单独加最大分
- **多进程安全**：同槽位同 key 全库只允许一条有效（部分唯一索引），
  撞约束自动重读收敛重试
- P2 批量：project=None/"" 跨后端归一、读路径深拷贝防污染、confidence=0.0 被吞、
  prune 幂等、序列化双轨统一（JSON 往返）、recent_ops/utterances 乱序不一致、
  recall(limit<=0) 硬塞一条、坏库隔离与坏 JSON 行加 RuntimeWarning、
  无 key 事实互相顶替代
- 写入性能：新增 `remember_many` 批量接口（整批一次落盘，1 万条 0.24s，
  原逐条 commit 约 17.7s）

### 文档与工程

- README 改英文为主入口，中文移至 README.zh-CN.md
- 新增"该选哪个记忆层"选型指南（vs Mem0 / memoripy v4 / 官方 MCP memory，
  附出处），明确各自适用场景
- 边界段补齐：换说法/中英混写召回不到的反例、0.4 衰减地板、锁的边界、
  批量导入指引、空查询语义
- pyproject：删除 voiceprint 关键词（库内无声纹功能），补 authors
- CI 加入第四道门 `tests/test_regressions.py`
- 性能对抗测试正名：唯一 key 真 2000 条 active，双后端都测

## [0.1.0] - 2026-11-19

首个公开版本：多人记忆层，逐句原话、按人隔离、证据链、把握度，
零依赖双后端（内存 / SQLite）。
