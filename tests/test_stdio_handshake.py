"""MCP stdio smoke test: spawns the real server over stdio, checks the tool
list, runs tools against real PDFs, and checks that instructive errors survive
the trip to the agent (MCPServer hides any exception that isn't a ToolError)."""

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

from conftest import make_text_pdf  # noqa: E402

EXPECTED_TOOLS = {
    "pdf_info",
    "read_pdf",
    "search_pdf",
    "extract_tables",
    "merge_pdfs",
    "split_pdf",
    "extract_pages",
    "rotate_pages",
    "extract_images",
}


def params(*dirs: Path) -> StdioServerParameters:
    args = ["-m", "pdf_toolkit_mcp"]
    for d in dirs:
        args += ["--dir", str(d)]
    return StdioServerParameters(command=sys.executable, args=args)


@pytest.mark.anyio
async def test_tools_and_a_session_over_stdio(tmp_path):
    pdf = make_text_pdf(tmp_path / "doc.pdf")
    async with stdio_client(params(tmp_path)) as (read, write), ClientSession(read, write) as s:
        init = await s.initialize()
        assert init.server_info.name == "pdf-toolkit"
        tools = (await s.list_tools()).tools
        assert {t.name for t in tools} == EXPECTED_TOOLS
        by_name = {t.name: t for t in tools}
        assert by_name["read_pdf"].annotations.read_only_hint is True
        assert by_name["merge_pdfs"].annotations.read_only_hint is False
        assert all(t.description and len(t.description) > 80 for t in tools)

        res = await s.call_tool("pdf_info", {"path": str(pdf)})
        assert not res.is_error and "Pages: 5" in res.content[0].text
        res = await s.call_tool("read_pdf", {"path": "doc.pdf", "pages": "2"})
        assert "zebra2" in res.content[0].text and "Çalışma" in res.content[0].text
        res = await s.call_tool("search_pdf", {"path": "doc.pdf", "query": "zebra4"})
        assert "Page 4:" in res.content[0].text
        res = await s.call_tool("split_pdf", {"path": "doc.pdf", "ranges": "1-2,5"})
        assert "2 file(s)" in res.content[0].text
        assert (tmp_path / "doc_part1_p1-2.pdf").exists()


@pytest.mark.anyio
async def test_instructive_errors_reach_the_agent(tmp_path):
    inside = tmp_path / "inside"
    inside.mkdir()
    make_text_pdf(inside / "doc.pdf")
    make_text_pdf(tmp_path / "outside.pdf")
    async with stdio_client(params(inside)) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        res = await s.call_tool("pdf_info", {"path": str(tmp_path / "outside.pdf")})
        assert res.is_error and "outside the allowed directories" in res.content[0].text
        res = await s.call_tool("read_pdf", {"path": "doc.pdf", "pages": "9"})
        assert res.is_error and "has 5 pages" in res.content[0].text
        res = await s.call_tool("extract_pages", {"path": "doc.pdf", "pages": "1"})
        assert not res.is_error
        res = await s.call_tool("extract_pages", {"path": "doc.pdf", "pages": "1"})
        assert res.is_error and "overwrite=true" in res.content[0].text
        res = await s.call_tool("pdf_info", {"path": "missing.pdf"})
        assert res.is_error and "File not found" in res.content[0].text


@pytest.mark.anyio
async def test_default_dir_is_cwd(tmp_path):
    make_text_pdf(tmp_path / "doc.pdf")
    p = StdioServerParameters(command=sys.executable, args=["-m", "pdf_toolkit_mcp"], cwd=str(tmp_path))
    async with stdio_client(p) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        res = await s.call_tool("pdf_info", {"path": "doc.pdf"})
        assert not res.is_error and "Pages: 5" in res.content[0].text


def test_bad_dir_exits_with_message(tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "pdf_toolkit_mcp", "--dir", str(tmp_path / "nope")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 2 and "does not exist" in r.stderr


@pytest.fixture
def anyio_backend():
    return "asyncio"
