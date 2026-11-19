# 召回质量公开评测

同一数据集、同一判定口径，各家记忆库自己跑、自己贴分数。
评测脚本：[recall_benchmark.py](recall_benchmark.py)（零依赖，即拷即跑）。

```bash
python benchmarks/recall_benchmark.py              # 只测 artl-memory
python benchmarks/recall_benchmark.py --semantic   # 加测一档语义插口（玩具 scorer）
python benchmarks/recall_benchmark.py --with-mem0  # 加测 Mem0（配置见下）
```

## 最新结果（2026-11-19，artl-memory 0.4.0，Windows，Python 3.14）

| 记忆库 | top1 命中 | 字面命中 | 改口时效 | 按人隔离 | 换说法 | 违规串供 |
|---|---|---|---|---|---|---|
| artl-memory 0.4.0（默认字词） | 9/10 | 3/3 | 1/1 | 3/3 | 2/3 | 0 |
| artl-memory 0.4.0 (+toy-scorer) | 10/10 | 3/3 | 1/1 | 3/3 | 3/3 | 0 |

唯一失分点：默认字词档查「装修大概要花多少钱」找不到「预算=三十五万」
（零字词重叠）——这是刻意的设计边界，装语义插口后救回。
toy-scorer 是演示插口协议的玩具 embedding，**不是严肃的语义分数**；
严肃数字请接真 embedding（`fastembed_scorer()` 或任意 embedding API）复跑。

## 公平性约定

做对比评测最容易被骂"配置不公平"，所以我们把规矩钉死：

1. **数据集公开**：3 个人物、12 条写入、10 个查询，全部写死在
   `recall_benchmark.py` 里，谁都能审、能改、能提 issue。
2. **裁判不是选手**：判定用字符串包含，不用 LLM 当裁判。
3. **各家用自己的设计哲学跑**：Mem0 走默认的 LLM 抽取式（那是它的设计，
   不是刁难），artl-memory 走原话记账。差异是设计差异，分数如实贴。
4. **换说法单独成列**：字面匹配库在这一项失分是预期行为，单列不混算，
   不许拿"总分"掩盖、也不许拿单项拉踩。
5. **版本写死**：结果表必须带各家的版本号和跑的时间。

## Mem0 对照怎么跑

```bash
pip install mem0ai
export OPENAI_API_KEY=...   # mem0 默认配置需要
python benchmarks/recall_benchmark.py --with-mem0
```

Mem0 是抽取式记忆（它替你把原话改写、合并、遗忘），这个 benchmark 里
"改口时效""按人隔离"两列对它最苛刻——如果你跑出了和我们预期不同的
结果，带版本号来提 issue，数据集该修就修。

## Graphiti 等其他库

Graphiti 需要 Neo4j 实例 + LLM，adapter 还没人写。协议就三个方法
（`add / recall / label`），照着 `Mem0Adapter` 抄一个即可，欢迎 PR。
