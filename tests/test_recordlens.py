import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_recordlens.server import inventory_folder, read_folder, search_folder, summarize_folder


class ScopeTests(unittest.TestCase):
    def test_folder_depth_exact_file_and_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deep = root / "one" / "two"
            deep.mkdir(parents=True)
            (root / "direct.csv").write_text("user\nAlice\n", encoding="utf-8")
            (deep / "nested.csv").write_text("user\nBob\n", encoding="utf-8")
            archive = root / "archive.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("a/b/c/d/users.csv", "user\nAlice\n")
            self.assertEqual(read_folder(directory)["returned_results"], 1)
            self.assertEqual(read_folder(directory, max_depth=1)["returned_results"], 1)
            self.assertEqual(read_folder(directory, max_depth=2)["returned_results"], 2)
            self.assertEqual(read_folder(str(deep / "nested.csv"))["returned_results"], 1)
            self.assertFalse(read_folder(str(archive), max_depth=1)["success"])
            found = search_folder(str(archive), field="user", value="Alice")
            self.assertEqual(found["returned_results"], 1, found)
            self.assertTrue(found["records"][0]["source_path"].endswith("!/a/b/c/d/users.csv"))
            self.assertEqual(summarize_folder(directory, "user", max_depth=2)["total_records"], 2)
            page = inventory_folder(directory, max_depth=2, max_files=1)
            self.assertEqual(page["next_offset"], 1)
            self.assertEqual(page["file_count"], 2)
            self.assertFalse(page["complete"])

    def test_xml_doctype_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.xml"
            path.write_text('<!DOCTYPE r [<!ENTITY test "text">]><r>&test;</r>', encoding="utf-8")
            output = read_folder(str(path))
            self.assertFalse(output["complete"])
            self.assertEqual(output["errors"][0]["code"], "xml_doctype_forbidden")

    def test_stdio_tool_contract(self):
        async def check():
            parameters = StdioServerParameters(
                command=sys.executable, args=["-m", "mcp_recordlens.server"]
            )
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual(len(tools.tools), 13)
                    health = await session.call_tool("get_server_health", {})
                    self.assertFalse(health.isError)
                    self.assertEqual((health.structuredContent or json.loads(health.content[0].text))["version"], "1.1.0")
        asyncio.run(check())
