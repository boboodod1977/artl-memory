# 召回质量公开评测

同一数据集、同一判定口径，各家记忆库自己跑、自己贴分数。
评测脚本：[recall_benchmark.py](recall_benchmark.py)（零依赖，即拷即跑）。

```bash
python benchmarks/recall_benchmark.py              # 只测 artl-memory
python benchmarks/recall_benchmark.py --semantic   # 加测一档语义插口（玩具 scorer）
python benchmarks/recall_benchmark.py --with-mem0  # 加测 Mem0（配置见下）
```

## 最新结果（2026-11-21，Windows，Python 3.14）

| 记忆库 | top1 命中 | 字面命中 | 改口时效 | 按人隔离 | 换说法 | 违规串供 |
|---|---|---|---|---|---|---|
| artl-memory 0.5.0（默认字词） | 9/10 | 3/3 | 1/1 | 3/3 | 2/3 | 0 |
| artl-memory 0.5.0 (+toy-scorer) | 10/10 | 3/3 | 1/1 | 3/3 | 3/3 | 0 |
| Mem0 2.2.1（infer=False 裸向量，bge-small-zh） | 7/10 | 2/3 | 0/1 | 3/3 | 2/3 | 2 |

Mem0 行的实测配置（全部可复跑）：

- **模式**：`infer=False` 裸向量——存取不走 LLM。为证明这一点，LLM 端点
  被故意指向 `http://127.0.0.1:1`（黑洞）：整个跑分过程它敢发一次请求
  就会立刻报错。零 LLM 调用是被这个设计强制证明的，不是我们声明的。
- **embedder**：fastembed `BAAI/bge-small-zh-v1.5`（本地，中文优化）
- **向量库**：qdrant 本地文件模式
- **抽取模式**（它的设计默认）需要任何 OpenAI 兼容 key，复跑方法见下；
  跑出来什么数字我们都如实贴

实测要点（都在 `bench_result` 的 details 里，可逐条核）：

- **改口时效 0/1**：王强改口"三十五万"后，问"王强的预算"，Mem0 裸向量档
  top1 返回的是旧的"三十万"——它两条都存，旧账还排在前面。
  artl-memory 的改口是"旧条失效、新条上位、证据链留痕"，这列是 1/1。
- **违规串供 2 次**：期望"三十五万"的查询结果里混着"三十万"。
- **按人隔离 3/3**：`filters={"user_id": ...}` 工作正常，这列它没有短板。
- artl-memory 默认字词档唯一失分：「装修大概要花多少钱」找不到
  「预算=三十五万」（零字词重叠）——刻意的设计边界，装语义插口后救回。
  toy-scorer 是演示插口协议的玩具 embedding，**不是严肃的语义分数**；
  严肃数字请接真 embedding（`fastembed_scorer()` 或任意 embedding API）复跑。

## 公平性约定

做对比评测最容易被骂"配置不公平"，所以我们把规矩钉死：

1. **数据集公开**：3 个人物、12 条写入、10 个查询，全部写死在
   `recall_benchmark.py` 里，谁都能审、能改、能提 issue。
2. **裁判不是选手**：判定用字符串包含，不用 LLM 当裁判。
3. **各家用自己的设计哲学跑**：Mem0 的抽取模式走它的 LLM 抽取流程
   （那是它的设计，不是刁难），artl-memory 走原话记账。
   差异是设计差异，分数如实贴。
4. **换说法单独成列**：字面匹配库在这一项失分是预期行为，单列不混算，
   不许拿"总分"掩盖、也不许拿单项拉踩。
5. **版本写死**：结果表必须带各家的版本号、模式和跑的时间。

## Mem0 对照怎么跑

```bash
pip install mem0ai fastembed

# 裸向量模式（无需任何 key，LLM 端点自动指向黑洞）：
python benchmarks/recall_benchmark.py --with-mem0

# 抽取模式（它的设计默认，任何 OpenAI 兼容端点都行）：
export MEM0_BENCH_LLM_API_KEY=sk-...
export MEM0_BENCH_LLM_BASE_URL=https://api.deepseek.com/v1   # 可选
export MEM0_BENCH_LLM_MODEL=deepseek-chat                    # 可选
python benchmarks/recall_benchmark.py --with-mem0
```

注意 adapter 面向 mem0 **2.x**（`filters={"user_id": ...}` 过滤）；1.x 的
`search(user_id=...)` 顶层参数在 2.x 已移除。如果你用 1.x 跑出了不同结果，
带版本号来提 issue，数据集该修就修。

## Graphiti 等其他库

Graphiti 需要 Neo4j 实例 + LLM，adapter 还没人写。协议就三个方法
（`add / recall / label`），照着 `Mem0Adapter` 抄一个即可，欢迎 PR。
