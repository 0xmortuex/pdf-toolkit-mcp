# pdf-toolkit-mcp

**Everyday PDF tasks for your AI agent, with nothing to install but Python.**

<!-- mcp-name: io.github.0xmortuex/pdf-toolkit-mcp -->

pdf-toolkit-mcp is an [MCP](https://modelcontextprotocol.io) server that lets an agent
look inside a PDF, read it in budget-sized pieces, search it, pull out tables and images,
and merge, split, reorder or rotate pages. It is built on pure-Python wheels only
([pypdf](https://pypdf.readthedocs.io) and [pdfplumber](https://github.com/jsvine/pdfplumber)),
so `uvx pdf-toolkit-mcp` works the same on Windows, macOS and Linux: no Java, no Ghostscript,
no Poppler, no Camelot, no compiler.

## What it looks like

```
> pdf_info report.pdf
Pages: 312
Encrypted: no
Text layer: yes (10/10 sampled pages have extractable text)
Page sizes (points, 72 per inch):
  595 x 842 (A4): pages 1-311
  842 x 595 (A4 landscape): pages 312
Outline / bookmarks:
  - Introduction (page 3)
  - Results (page 41)
    - Revenue by region (page 44)

> read_pdf report.pdf pages="41-"
--- Page 41 of 312 ---
...
[Output truncated at about 8000 tokens. Continue with pages="47-312" (266 more page(s), starting at page 47).]

> search_pdf report.pdf query="net revenue"
14 match(es) on 6 page(s) (44-46,88,201-202):
Page 44: ...Q3 net revenue rose 12% to 4.1M, driven by...

> read_pdf scan.pdf
WARNING: none of the selected pages has a text layer. This PDF looks scanned (images
only); extracting text needs OCR, which this server does not do.
```

## Install

You need [uv](https://docs.astral.sh/uv/) (or `pip install pdf-toolkit-mcp`).

**Claude Code**

```bash
claude mcp add pdf -- uvx pdf-toolkit-mcp --dir ~/Documents --dir ~/Downloads
```

**Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "pdf": {
      "command": "uvx",
      "args": ["pdf-toolkit-mcp", "--dir", "C:\\Users\\you\\Documents", "--dir", "C:\\Users\\you\\Downloads"]
    }
  }
}
```

The server can only touch the folders you pass with `--dir` (repeat the flag for more). With
no `--dir` it uses the folder it was started in, which for Claude Desktop is rarely where your
PDFs are, so pass at least one.

## Tools

| Tool | What it does |
|------|--------------|
| `pdf_info` | Pages, metadata, encrypted?, has a text layer?, page sizes, outline with page numbers. Call first. |
| `read_pdf` | Text by page range, optional layout mode, token budget with a "continue with pages=..." hint, clear notice for scanned pages. |
| `search_pdf` | Matches with page numbers and context snippets; plain (case-insensitive, line-wrap tolerant) or regex. |
| `extract_tables` | Tables as markdown or CSV text; `lines` strategy for ruled tables, `text` for borderless ones. |
| `merge_pdfs` | Concatenate PDFs in order, keeping bookmarks. |
| `split_pdf` | One file per range part: `1-3,5,7-` makes three files. |
| `extract_pages` | New PDF from chosen pages, in the order written (reorders and repeats allowed). |
| `rotate_pages` | Rotate chosen pages by a multiple of 90 degrees. |
| `extract_images` | Save embedded images to a folder. |

Page ranges are 1-based: `1-3,5,7-` (`7-` means page 7 to the end).

## Try asking

- "What is in `~/Downloads/contract.pdf`? How many pages, and does it have bookmarks?"
- "Find every mention of 'termination' in the contract and tell me which pages."
- "Read pages 12 to 15 of the annual report and summarise the revenue section."
- "Pull the tables from pages 30-35 of `statement.pdf` as CSV."
- "Split `book.pdf` into chapters: 1-24, 25-60 and 61 to the end."
- "Merge `cover.pdf`, `body.pdf` and `appendix.pdf` into `final.pdf`."
- "Page 4 of `scan-001.pdf` is sideways. Rotate it and save a fixed copy."
- "Pull all the photos out of `brochure.pdf`."
- "This PDF is password protected, the password is ... Read the first page."

## Safety

- **Folder allow-list.** Every read and every write must resolve inside a `--dir` folder.
  `..` tricks, symlinks that point outside and sibling folders with a similar name are
  all rejected.
- **No silent overwrites.** Write tools refuse to replace an existing file unless the call
  passes `overwrite=true`. `split_pdf` checks every output name before writing any, and
  overwrites go through a temporary file so a failure cannot leave a half-written PDF.
- **Originals are never modified** unless you point `output_path` at the input and set
  `overwrite=true` yourself.
- **Passwords** are a call parameter. They are handed to the PDF library and nowhere else:
  never logged, stored, or included in an error message. Output PDFs are not encrypted.
- Errors are explicit and say what to do next (wrong password, page out of range, file
  exists, path outside the allowed folders). Nothing falls back silently.

## Limits

- **No OCR.** A scanned PDF has no text to extract. The tools detect this and say so rather
  than return an empty string; use an OCR tool on the page images (`extract_images` can
  export them).
- Text extraction follows the PDF's own text layer. Multi-column pages can interleave;
  `layout=true` often helps.
- Table detection is heuristic. Ruled tables are reliable; borderless ones need
  `strategy="text"` and may need checking.
- Encrypted outputs, form filling, annotations and redaction are out of scope.

## Tests

```bash
uv sync --extra test
uv run pytest tests
```

The tests generate real PDFs with reportlab (multi-page, Turkish text, ruled and borderless
tables, bookmarks, AES-256 encryption, image-only scans, a 300-page document), run every
tool against them, and drive the installed server over a real MCP stdio session.

## License

MIT
