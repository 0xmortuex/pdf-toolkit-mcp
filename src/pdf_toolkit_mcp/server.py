"""pdf-toolkit-mcp: an MCP server for everyday PDF tasks."""

from __future__ import annotations

import argparse
import functools
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, core
from .core import PdfToolError, Sandbox

mcp = MCPServer(
    "pdf-toolkit",
    instructions=(
        "Everyday PDF tasks. Start with pdf_info (page count, text layer, bookmarks), then "
        "read_pdf for text (it truncates by token budget and tells you which pages to ask for "
        "next), search_pdf to find pages, extract_tables for tables. merge_pdfs, split_pdf, "
        "extract_pages, rotate_pages and extract_images write new files and never overwrite "
        "unless overwrite=true. All paths must be inside the allowed folders (relative paths "
        "start at the first one). Page ranges are 1-based like '1-3,5,7-'. Encrypted PDFs need "
        "the password parameter. Scanned (image-only) pages have no text and need OCR, which "
        "this server does not do; the tools say so when they see one."
    ),
)

_sandbox: Sandbox | None = None

F = TypeVar("F", bound=Callable[..., Any])

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)


def _sb() -> Sandbox:
    global _sandbox
    if _sandbox is None:
        _sandbox = Sandbox()
    return _sandbox


def tool(annotations: ToolAnnotations) -> Callable[[F], F]:
    """Register fn as an MCP tool, turning expected failures into ToolErrors.

    MCPServer only forwards a ToolError's message to the client; any other
    exception arrives as a bare "Error executing tool". Expected failures
    (PdfToolError, OS errors on user-supplied paths) keep their text this way.
    """

    def deco(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except PdfToolError as e:
                raise ToolError(str(e)) from e
            except OSError as e:
                raise ToolError(f"File system error: {e}") from e

        mcp.tool(annotations=annotations)(wrapper)
        return fn

    return deco


@tool(READ_ONLY)
def pdf_info(path: str, password: str | None = None) -> str:
    """Summarise a PDF: page count, metadata, whether it is encrypted, whether it has a text
    layer (a scanned PDF does not), page sizes, and the outline/bookmarks with page numbers.
    Call this first to decide how to read the file.

    path: PDF file inside an allowed folder (absolute, or relative to the first allowed folder).
    password: only for encrypted PDFs. Without it an encrypted file just reports that it is locked.
    """
    return core.pdf_info(_sb(), path, password)


@tool(READ_ONLY)
def read_pdf(
    path: str,
    pages: str | None = None,
    layout: bool = False,
    max_tokens: int = 8000,
    password: str | None = None,
) -> str:
    """Read the text of a PDF, page by page, each page headed '--- Page N of M ---'.

    Output is capped at roughly max_tokens. When it is cut off, the last line says exactly which
    pages= value to pass to continue. Pages without a text layer (scanned images) are reported
    as such instead of coming back empty; there is no OCR.

    pages: 1-based ranges like '1-3,5,7-' ('7-' = page 7 to the end). Empty = all pages.
    layout: true keeps the visual column layout (better for forms and statements; wider output).
    max_tokens: output budget, about 4 characters per token.
    """
    return core.read_pdf(_sb(), path, pages, layout, max_tokens, password)


@tool(READ_ONLY)
def search_pdf(
    path: str,
    query: str,
    pages: str | None = None,
    case_sensitive: bool = False,
    regex: bool = False,
    context_chars: int = 80,
    max_results: int = 30,
    password: str | None = None,
) -> str:
    """Find text in a PDF. Returns each match as 'Page N: ...context...' plus the list of pages hit.
    Use it to locate the right pages, then read_pdf with pages=. A plain query matches across line
    breaks and ignores case; set regex=true for a regular expression.

    pages: optional 1-based ranges like '1-3,5,7-' to limit the search.
    context_chars: characters of surrounding text on each side of a match.
    max_results: snippets to return (the total match count is always reported).
    """
    return core.search_pdf(
        _sb(), path, query, pages, case_sensitive, regex, context_chars, max_results, password
    )


@tool(READ_ONLY)
def extract_tables(
    path: str,
    pages: str | None = None,
    format: Literal["markdown", "csv"] = "markdown",
    strategy: Literal["lines", "text"] = "lines",
    max_tokens: int = 8000,
    password: str | None = None,
) -> str:
    """Extract tables from a PDF as markdown or CSV text, labelled with page number and size.

    strategy 'lines' (default) finds tables drawn with ruling lines; use 'text' for borderless
    tables aligned by whitespace (less precise). Scanned PDFs have no tables to find.
    Output is capped at roughly max_tokens with a continue hint, like read_pdf.

    pages: 1-based ranges like '2-4,9'. Limiting pages is much faster on big files.
    """
    return core.extract_tables(_sb(), path, pages, format, strategy, max_tokens, password)


@tool(WRITES)
def merge_pdfs(
    paths: list[str],
    output_path: str | None = None,
    overwrite: bool = False,
    password: str | None = None,
) -> str:
    """Concatenate PDFs in the order given into one new file (bookmarks are kept).

    output_path: must end in .pdf. Default: merged.pdf next to the first input.
    overwrite: replace the output if it exists. Default false: an existing file is an error.
    password: if any input is encrypted (one password for all; the output is not encrypted).
    """
    return core.merge_pdfs(_sb(), paths, output_path, overwrite, password)


@tool(WRITES)
def split_pdf(
    path: str,
    ranges: str,
    output_dir: str | None = None,
    overwrite: bool = False,
    password: str | None = None,
) -> str:
    """Split a PDF into several files. Each comma-separated part of ranges becomes one file,
    e.g. '1-3,5,7-' makes three files (pages 1-3, page 5, page 7 to the end). Files are named
    <name>_part<k>_p<pages>.pdf. The original is untouched. All names are checked before anything
    is written, so a conflict never leaves a half-done split.

    output_dir: default is the input's folder. Created if missing.
    overwrite: replace existing outputs. Default false.
    """
    return core.split_pdf(_sb(), path, ranges, output_dir, overwrite, password)


@tool(WRITES)
def extract_pages(
    path: str,
    pages: str,
    output_path: str | None = None,
    overwrite: bool = False,
    password: str | None = None,
) -> str:
    """Copy selected pages into one new PDF, in the order written ('3,1,2' reorders; '2,2' repeats).

    output_path: must end in .pdf. Default: <name>_extracted.pdf next to the input.
    overwrite: replace the output if it exists. Default false.
    """
    return core.extract_pages(_sb(), path, pages, output_path, overwrite, password)


@tool(WRITES)
def rotate_pages(
    path: str,
    degrees: int,
    pages: str | None = None,
    output_path: str | None = None,
    overwrite: bool = False,
    password: str | None = None,
) -> str:
    """Rotate pages and save a new PDF. degrees is a multiple of 90, clockwise (90, 180, 270, or -90
    for counter-clockwise). pages empty = every page.

    output_path: must end in .pdf. Default: <name>_rotated.pdf next to the input. To rotate a file
    in place, pass its own path as output_path together with overwrite=true.
    """
    return core.rotate_pages(_sb(), path, degrees, pages, output_path, overwrite, password)


@tool(WRITES)
def extract_images(
    path: str,
    pages: str | None = None,
    output_dir: str | None = None,
    overwrite: bool = False,
    max_images: int = 200,
    password: str | None = None,
) -> str:
    """Save the images embedded in a PDF (photos, scans, logos) as files named
    <name>_p<page>_<k>.<ext>, in their original format where possible. Lists the files written.
    Vector drawings and text are not images and are not extracted.

    output_dir: default <name>_images next to the input. Created if missing.
    max_images: safety cap; the call fails (writing nothing) if more are found.
    """
    return core.extract_images(_sb(), path, pages, output_dir, overwrite, max_images, password)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="pdf-toolkit-mcp",
        description="MCP server for everyday PDF tasks (stdio).",
    )
    ap.add_argument(
        "--dir",
        action="append",
        default=[],
        metavar="FOLDER",
        help="folder the server may read and write (repeatable; default: the current folder)",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def main(argv: list[str] | None = None) -> None:
    global _sandbox
    args = build_parser().parse_args(argv)
    try:
        _sandbox = Sandbox([Path(d) for d in args.dir])
    except PdfToolError as e:
        print(f"pdf-toolkit-mcp: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    mcp.run()


if __name__ == "__main__":
    main()
