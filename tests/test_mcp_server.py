# -*- coding: utf-8 -*-
"""MCP 挂件测试。

分两层：
    第一层（总是跑）：工具实现是纯函数 + MemoryBase，不 import mcp 包，
                     核心零依赖的承诺不许被挂件打破。
    第二层（装了 mcp 才跑）：FastMCP 注册检查 + 真 stdio 握手——
                     起一个 `python -m artl_memory.contrib.mcp_server`
                     子进程，走完整 MCP 协议调用 remember/recall。

跑法：
    python tests/test_mcp_server.py          # 独立运行
    pip install "artl-memory[mcp]" 后协议层自动启用
"""

import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artl_memory.contrib import mcp_server

HAS_MCP = importlib.util.find_spec("mcp") is not None


def _base():
    tmp = Path(tempfile.mkdtemp(prefix="artl-mcp-test-"))
    return mcp_server.MemoryBase(tmp / "t.db")


# ---------------------------------------------------------------------------
# 第一层：零依赖逻辑测试
# ---------------------------------------------------------------------------

class ToolLogicTests(unittest.TestCase):
    def setUp(self):
        self.base = _base()

    def tearDown(self):
        self.base.close()

    def test_remember_recall_roundtrip(self):
        r = mcp_server.tool_remember(self.base, "wang-qiang", "budget", "300k",
                                     source="utterance:wq-001", confidence=0.92)
        self.assertTrue(r["ok"])
        self.assertTrue(r["created"])
        hits = mcp_server.tool_recall(self.base, "wang-qiang", "budget")
        self.assertEqual(hits[0]["value"], "300k")
        self.assertEqual(hits[0]["source_event"], "utterance:wq-001")
        self.assertIn("recall_detail", hits[0],
                      "召回必须带打分因子——为什么想起这条，永远可查")

    def test_dedupe_and_supersede_chain(self):
        r1 = mcp_server.tool_remember(self.base, "wang-qiang", "budget", "300k",
                                      source="utterance:wq-001")
        r2 = mcp_server.tool_remember(self.base, "wang-qiang", "budget", "300k",
                                      source="utterance:wq-001")
        self.assertFalse(r2["created"], "同值同 key 重复写必须命中去重")
        self.assertEqual(r1["id"], r2["id"])
        r3 = mcp_server.tool_remember(self.base, "wang-qiang", "budget", "350k",
                                      source="utterance:wq-002")
        self.assertTrue(r3["created"])
        self.assertEqual(r3["supersedes"], r1["id"], "改口必须指向旧条目")
        chain = mcp_server.tool_evidence_chain(self.base, "wang-qiang", r3["id"])
        self.assertEqual(len(chain), 2)
        self.assertEqual([c["value"] for c in chain], ["350k", "300k"])
        self.assertTrue(chain[1]["valid_to"], "旧条目必须带着失效时间")

    def test_isolation_between_people(self):
        mcp_server.tool_remember(self.base, "wang-qiang", "budget", "300k")
        mcp_server.tool_remember(self.base, "li-si", "budget", "80k")
        hits = mcp_server.tool_recall(self.base, "li-si", "budget")
        self.assertEqual([h["value"] for h in hits], ["80k"])
        self.assertNotIn("300k", [str(h["value"]) for h in hits],
                         "李四的召回里不许出现王强的数字")

    def test_recall_all_tags_person(self):
        mcp_server.tool_remember(self.base, "wang-qiang", "budget", "300k",
                                 confidence=0.92)
        mcp_server.tool_remember(self.base, "li-si", "budget", "80k",
                                 confidence=0.90)
        hits = mcp_server.tool_recall_all(self.base, "budget")
        people = {h["person"] for h in hits}
        self.assertEqual(people, {"wang-qiang", "li-si"},
                         "跨人召回必须标明每条是谁的")

    def test_list_people_and_purge_is_physical(self):
        mcp_server.tool_remember(self.base, "wang-qiang", "budget", "300k",
                                 source="utterance:wq-001")
        mcp_server.tool_remember(self.base, "li-si", "budget", "80k")
        self.assertEqual(mcp_server.tool_list_people(self.base),
                         ["li-si", "wang-qiang"])
        counts = mcp_server.tool_purge_person(self.base, "wang-qiang")
        self.assertGreaterEqual(counts.get("memory_items", 0), 1)
        self.assertEqual(mcp_server.tool_list_people(self.base), ["li-si"])
        # 物理删除的字节级验证：关掉连接后扫库文件，"300k" 一次都不许出现
        db = Path(self.base.db_path)
        self.base.close()
        data = b""
        for suffix in ("", "-wal", "-shm"):
            blob = db.parent / (db.name + suffix)
            if blob.exists():
                data += blob.read_bytes()
        self.assertEqual(data.count("300k".encode("utf-8")), 0,
                         "purge 之后库文件里不许还剩明文")
        self.base = _base()  # tearDown 需要一个可用的 base

    def test_forget_expires_but_keeps_evidence(self):
        r = mcp_server.tool_remember(self.base, "zhao-liu", "taste",
                                     "extra cilantro")
        out = mcp_server.tool_forget(self.base, "zhao-liu", r["id"])
        self.assertTrue(out["ok"])
        self.assertEqual(mcp_server.tool_recall(self.base, "zhao-liu", "taste"),
                         [], "失效后召回不到")
        chain = mcp_server.tool_evidence_chain(self.base, "zhao-liu", r["id"])
        self.assertEqual(len(chain), 1, "失效不是删除——条目还在证据链里")

    def test_export_person(self):
        mcp_server.tool_remember(self.base, "zhao-liu", "taste",
                                 "extra cilantro", source="utterance:zl-001")
        data = mcp_server.tool_export_person(self.base, "zhao-liu")
        self.assertEqual(data["format"], "artl-memory-export")
        self.assertEqual(data["designer"], "zhao-liu")
        self.assertEqual(data["counts"]["memory_items"], 1)

    def test_empty_person_rejected(self):
        with self.assertRaises(ValueError):
            mcp_server.tool_remember(self.base, "  ", "k", "v")
        with self.assertRaises(ValueError):
            mcp_server.tool_purge_person(self.base, "")


# ---------------------------------------------------------------------------
# 第二层：协议测试（装了 mcp 才跑）
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MCP, "mcp 包未安装（pip install artl-memory[mcp]）")
class ProtocolTests(unittest.TestCase):
    def test_fastmcp_tools_registered(self):
        base = _base()
        try:
            app = mcp_server.create_app(base)
            tools = asyncio.run(app.list_tools())
            names = {t.name for t in tools}
            self.assertEqual(names, {"remember", "recall", "recall_all",
                                     "list_people", "evidence_chain",
                                     "forget", "purge_person",
                                     "export_person"})
        finally:
            base.close()

    def test_stdio_roundtrip(self):
        """真起一个 server 子进程，走完整 MCP stdio 握手和调用。"""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        tmp = Path(tempfile.mkdtemp(prefix="artl-mcp-stdio-"))
        db = str(tmp / "roundtrip.db")
        env = dict(os.environ)
        # 子进程要能找到仓库里的 artl_memory（没安装的场景，比如 CI 源码树）
        root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")

        async def run():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "artl_memory.contrib.mcp_server", "--db", db],
                env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await session.call_tool("remember", {
                        "person": "wang-qiang", "key": "budget",
                        "value": "300k", "source": "utterance:wq-001",
                        "confidence": 0.92})
                    result = await session.call_tool("recall", {
                        "person": "wang-qiang", "query": "budget"})
                    return result

        result = asyncio.run(run())
        self.assertFalse(result.isError, "recall 调用失败: %r" % (result,))
        text = "".join(getattr(c, "text", "") for c in result.content)
        self.assertIn("300k", text)
        self.assertIn("utterance:wq-001", text,
                      "协议层必须把来源一起带回来")


if __name__ == "__main__":
    unittest.main(verbosity=2)
