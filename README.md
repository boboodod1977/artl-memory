# artl-memory

**The multi-person memory layer for agents.**
Verbatim utterances · per-person isolation · evidence chains · confidence scores.

> It remembers everyone in the room — separately.
> We don't promise understanding. We promise not to forget.

[中文文档](README.zh-CN.md)

artl-memory is extracted from a production assistant. It does **not** "summarize a
profile of you and pretend to know you" — it stores **who said exactly what, and when**,
each memory carrying its source utterance and a confidence score. When a fact is
corrected, the old entry is marked expired — never silently overwritten — and the
evidence chain stays in place.

> **Scope: the ledger, not the ears.** artl-memory contains no audio, no ASR, no
> voiceprint. Your upstream pipeline (diarization, voiceprint, manual labels)
> decides *who* said what and hands this library the name — it keeps the books.
> That is what makes it dependency-free: usable without ears, plug-and-play with
> them.

```bash
pip install artl-memory
```

## Which memory layer should you pick?

Everyone arriving here has Mem0 (~60k★) as their reference point, so let's be direct
about it. These four tools answer **different questions** — this table is for picking
the right one, not for ranking them.

| | **artl-memory** | **Mem0** | **memoripy v4** | **MCP memory (official)** |
|---|---|---|---|---|
| Needs an LLM to write memory | **No** — zero model calls | Yes — extraction LLM on every `add()` | No required — deterministic extractor, bring-your-own optional | No — the agent itself decides what to store |
| What gets stored | **Verbatim utterances** + derived facts | LLM-extracted summaries | Evidence + derived memory, versioned | Entities / relations / observations |
| Can you trace a memory to its source? | Yes — `source_event` + confidence on every entry | Partial — extraction rewrites wording | Yes — citations and receipts | No |
| Multi-person isolation | **First-class** — per-person ledger, skills get scoped `MemView`s | Per-`user_id` scoping | Per-`user_id` scoping | No — one shared graph |
| Deletion | **Hard delete** — `secure_delete` + `VACUUM` + WAL truncation, verified by byte-level scan | API delete; vector-store semantics | `forget` tooling | Delete tools on the JSONL file |
| Works fully offline | Yes | No (LLM + vector store calls) | Yes | Yes |
| Semantic (vector) search | **Optional hook** — explainable bigram by default; `[semantic]` extra plugs in embeddings | Yes | Optional embeddings | No — substring match |
| Dependencies | **0** (stdlib `sqlite3`) | LLM client + vector DB + embedder | 0 required (v4) | Node.js runtime |

Be fair to each of them:

- **Pick Mem0** if you want automatic "talk → extracted profile" with semantic search,
  mature framework integrations (LangChain, CrewAI, AutoGen) and published benchmark
  numbers. That's a lot of real value. The trade-offs are documented by its own
  community: an audit of 10,134 Mem0 entries found 97.8% junk, including hallucinated
  user profiles that get re-extracted and amplified across sessions
  ([mem0#4573](https://github.com/mem0ai/mem0/issues/4573)); the HaluMem benchmark
  found extraction-stage hallucination is systemic across LLM-based memory systems
  ([arXiv:2511.03506](https://arxiv.org/abs/2511.03506)). If the wording of a memory
  doesn't have to be provably what a human said, that trade may be fine for you.
- **Pick memoripy** if you like the evidence-first philosophy on a **single-user**
  workload and want an official MCP server out of the box.
- **Pick the official MCP memory server** if you need a minimal scratchpad for one
  agent on one machine. It calls itself a reference implementation, and it is honest
  about being one.
- **Pick artl-memory** if the question is *"several people talk to one agent — in a
  meeting room, a family, a mediation session — and the record has to hold up later"*:
  who said which exact sentence, how confident was the transcription, what did the
  agent believe before the correction, and can 王强's utterances really be erased
  byte-for-byte when he asks. If a smart profile matters more than a trustworthy
  ledger, we are the wrong tool — and this README will tell you so again below.

## Why multi-person

Most agent memory systems are **single-user** models: one user, one profile.
artl-memory was a multi-person model from day one:

- **No headcount cap**: per-person ledgers mean recalling for 100 people costs the
  same as for 1 — everyone only scans their own books. Measured: 100 people ×
  200 entries (20k rows), median recall 0.4ms, zero isolation violations
  (Windows, Python 3.14)

- **Per-person isolation**: 王强's utterances never leak into 李四's recall; skills and
  plugins only see their own slot through `MemView`, which holds no store handle
  (same-process introspection can still dig — for hostile code, use process isolation)
- **Verbatim first**: the original sentence is stored before any judgement; rewriting
  history does not exist on this path
- **Evidence chains**: every memory carries `source_event` (which utterance it came
  from) and `confidence`; corrections expire the old entry and link it via
  `supersedes` — **expiry is not deletion**
- **Time-based downgrade**: utterances older than N days are pruned — unreferenced
  rows are deleted, referenced rows keep their snippet so the chain never dangles
- **Right to be forgotten**: `store.purge_designer("王强")` physically erases all five
  tables' worth of his data (`PRAGMA secure_delete` + `VACUUM` + WAL truncation),
  and returns per-table deletion counts

## Quickstart (30 seconds)

```python
from artl_memory import SqliteMemoryStore, MemoryService, MemView

store = SqliteMemoryStore("memory.db")
mem = MemoryService(store, designer="heima")

mem.remember("preference", "王强:预算", "三十万",
             source="utterance:wq-001", confidence=0.9)
mem.recall("王强的预算")        # relevance × confidence × time decay — every factor explainable

view = MemView(store, owner="skill:render", designer="heima")
view.put("风格", "奶油风")       # this skill's memory; nobody else's gets in
```

Run the multi-person example:

```bash
python examples/two_people.py
```

## Zero dependencies, two backends

- **Zero hard dependencies**: standard library only (`sqlite3` is built in). A memory
  layer should not drag a dependency tree through your door.
- `InMemoryStore` for tests and trials (not thread-safe); `SqliteMemoryStore` with
  WAL, single connection, auto-reopen after close, corrupt-file quarantine, and a
  versioned schema.
- The same conformance suite runs against both backends:
  `python tests/test_conformance.py` (pytest-compatible)
- Adversarial tests cover "undocumented but must not explode":
  `python tests/test_adversarial.py`
- Regression tests pin every bug ever found by external review:
  `python tests/test_regressions.py`

## Optional semantic recall (0.3+)

The core stays zero-dependency. "Understanding paraphrases" is an optional socket —
the same philosophy as *the ledger, not the ears*: bring your own smarts, the books
stay untouched.

```python
from artl_memory.contrib import EmbeddingScorer, fastembed_scorer

mem = MemoryService(store, designer="王强",
                    recall_scorer=fastembed_scorer())   # local bge-small-zh
# or any embedding API:
mem2 = MemoryService(store, designer="王强",
                     recall_scorer=EmbeddingScorer(your_batch_embed_fn))
```

- **Red line: the scorer finds, it never writes.** It can only re-rank candidates
  that already exist in the store — unknown ids are dropped. Utterances, evidence
  chains and confidence stay in the core's custody
- The merge is public: `relevance = word_score + semantic_weight × semantic_score`
  (default 3.0); zero-overlap entries enter results only when their semantic score
  ≥ `semantic_min` (default 0.5)
- Every result carries `recall_detail` — `word / semantic / confidence / decay` as
  separate factors, so *why this entry* stays answerable even with semantics on
- Scorer down (API outage)? Warning + fall back to word-only scoring — recall
  never dies with the plugin
- `pip install artl-memory[semantic]`; without it the core is pure stdlib and
  behavior is byte-identical

## Evidence chains & GDPR (0.4+)

```python
chain = mem.evidence_chain(item_id)      # [now, ..., oldest] — source & expiry on every link
data  = mem.export_designer("王强.json") # all five tables as UTF-8 JSON (right to access)
store.purge_designer("王强")             # physical hard delete, byte-level verified (right to erasure)
```

Access and erasure as a closed pair; a regression test pins "export after purge
must be empty".

The **public recall benchmark** lives in [benchmarks/](benchmarks/): 3 people,
12 writes, 10 queries — all pinned in the script, judged by substring match (the
referee is never a contestant), with literal-hit / correction-recency /
isolation / paraphrase scored separately. A Mem0 comparison adapter is one
flag away.

## Boundaries (stated plainly)

- Values are stored JSON-serialized: `tuple` comes back as `list`; serialize custom
  objects yourself.
- `recall` is O(n) full-scan scoring — deliberately index-free for explainability.
  The scan window is `MemoryService(recall_window=...)`, default 10000 entries; when
  the window fills up you get a loud `RuntimeWarning`, never silent truncation.
  The window only truncates keyless chatter: the newest active entry of every
  non-empty key always participates in scoring (0.3+) — "the current value of this
  slot" can't be pushed out by noise.
  For bulk imports use `remember_many(...)` (one fsync per batch, ~100x faster than
  per-item commits) and make sure the window exceeds your total entry count.
- **By default recall scores by literal token overlap; it does not paraphrase.**
  Rephrasing or mixing languages will miss: `who pays thirty万 for the living room`
  does not recall the Chinese entry「三十万」. Need semantics? Use the optional hook
  (0.3+): `pip install artl-memory[semantic]`, or `EmbeddingScorer(your_embed_fn)`
  for any embedding API — bring your own engine, the core stays clean.
- Time decay floors at 0.4: very old high-confidence entries keep participating in
  ranking, just discounted. To retire an old memory from ranking entirely, `forget`
  it (expiry, not deletion).
- Empty query returns an empty list — when there is no question, it does not pretend
  to understand one.
- Lock boundaries: `MemoryService.remember` is thread-safe via the service lock;
  `InMemoryStore` itself is not thread-safe — use it through the service layer.
  Multi-process writes to the same SQLite file are guarded by database-level unique
  constraints (only one active entry per slot+key); the loser re-reads and
  converges automatically.
- Isolation is at the API surface. Nothing in-process is cryptographically hidden —
  if plugins are adversarial, put them behind a process boundary.

## Explicitly not doing

- **No built-in vector retrieval in the core.** bigram recall is deliberate: you
  can always see *why* it remembered this entry. Need semantics? There's an
  optional hook (0.3+) — the engine is yours, the core stays untouched.
- **No cloud sync, no multi-machine.** Your data is your file. Feature, not bug.
- **No LLM calls.** The memory layer doesn't know LLMs exist — swap brains freely,
  the memory stays.

## License

Apache-2.0 · ARTL family
