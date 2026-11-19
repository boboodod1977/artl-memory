# Changelog

本项目遵循语义化版本。0.x 阶段： minor 可能含不兼容变更，patch 只修 bug。

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
