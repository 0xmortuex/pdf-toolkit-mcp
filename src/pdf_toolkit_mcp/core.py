"""PDF operations behind the MCP tools.

Everything here is plain Python: functions take a ``Sandbox`` (the allowed
directories), validate paths, do the work with pypdf / pdfplumber, and return
text for the agent. Expected failures raise ``PdfToolError`` with a message that
says what to do next; the server layer turns those into MCP tool errors.
Passwords are only ever passed to the PDF libraries - they are never logged,
stored or included in a message.
"""

from __future__ import annotations

import csv
import io
import os
import re
from pathlib import Path
from typing import Any

import pdfplumber
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError
from pypdf.generic import Destination

CHARS_PER_TOKEN = 4  # rough rule of thumb; good enough for budgeting output

# Errors the PDF libraries raise on damaged or unusual files.
_PDF_ERRORS = (PyPdfError, ValueError, KeyError, TypeError, AttributeError, NotImplementedError)


class PdfToolError(Exception):
    """An expected failure whose message is safe and useful to show the agent."""


class PasswordRequired(PdfToolError):
    """The PDF is encrypted and no (or an empty) password was given."""


# --------------------------------------------------------------------------
# Paths and the allowed-directory sandbox
# --------------------------------------------------------------------------


def _norm(p: Path) -> str:
    return os.path.normcase(str(p))


class Sandbox:
    """Restricts every read and write to a list of allowed directories."""

    def __init__(self, dirs: list[Path] | None = None) -> None:
        raw = dirs or [Path.cwd()]
        resolved: list[Path] = []
        for d in raw:
            rd = Path(d).expanduser().resolve()
            if not rd.is_dir():
                raise PdfToolError(f"Allowed directory does not exist: {d}")
            resolved.append(rd)
        self.dirs = resolved

    def _inside(self, p: Path) -> bool:
        n = _norm(p)
        for d in self.dirs:
            nd = _norm(d)
            if n == nd or n.startswith(nd.rstrip("\\/") + os.sep):
                return True
        return False

    def resolve(self, raw: str, what: str = "path") -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise PdfToolError(f"{what} must be a non-empty path.")
        p = Path(raw.strip()).expanduser()
        if not p.is_absolute():
            p = self.dirs[0] / p
        p = p.resolve()
        if not self._inside(p):
            allowed = "; ".join(str(d) for d in self.dirs)
            raise PdfToolError(
                f"{what} '{raw}' is outside the allowed directories ({allowed}). "
                "The server owner must start it with --dir <folder> to allow more."
            )
        return p

    def input_pdf(self, raw: str) -> Path:
        p = self.resolve(raw, "path")
        if not p.exists():
            raise PdfToolError(f"File not found: {p}")
        if not p.is_file():
            raise PdfToolError(f"Not a file: {p}")
        return p

    def output_pdf(self, raw: str | None, default: Path) -> Path:
        p = self.resolve(raw, "output_path") if raw else default
        if p.suffix.lower() != ".pdf":
            raise PdfToolError(f"Output file must end in .pdf, got: {p.name}")
        if not p.parent.is_dir():
            raise PdfToolError(f"Output folder does not exist: {p.parent}")
        return p

    def output_dir(self, raw: str | None, default: Path) -> Path:
        p = self.resolve(raw, "output_dir") if raw else default
        if p.exists() and not p.is_dir():
            raise PdfToolError(f"output_dir exists but is not a folder: {p}")
        return p


def check_free(out: Path, overwrite: bool) -> None:
    if out.is_dir():
        raise PdfToolError(f"Output path is a folder, not a file: {out}")
    if out.exists() and not overwrite:
        raise PdfToolError(
            f"Refusing to overwrite existing file {out}. "
            "Pass overwrite=true to replace it, or choose a different output path."
        )


def write_bytes(out: Path, data: bytes, overwrite: bool) -> None:
    """Write without ever clobbering a file unless overwrite is true."""
    if not overwrite:
        try:
            with open(out, "xb") as f:
                f.write(data)
        except FileExistsError:
            check_free(out, False)
        return
    tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out)


def write_pdf(writer: PdfWriter, out: Path, overwrite: bool) -> None:
    buf = io.BytesIO()
    writer.write(buf)
    write_bytes(out, buf.getvalue(), overwrite)


# --------------------------------------------------------------------------
# Opening PDFs and page selections
# --------------------------------------------------------------------------


def open_reader(path: Path, password: str | None = None) -> PdfReader:
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            # An empty user password is common ("owner-locked" files); try it first.
            if not reader.decrypt(password or ""):
                if password:
                    raise PdfToolError(f"The password for {path.name} is wrong.")
                raise PasswordRequired(
                    f"{path.name} is encrypted. Retry with the password parameter."
                )
        # Touch the page tree now so a corrupt file fails here, with a clear message.
        len(reader.pages)
    except PdfToolError:
        raise
    except _PDF_ERRORS as e:
        raise PdfToolError(f"{path.name} could not be read as a PDF: {e}") from e
    return reader


def parse_pages(spec: str | None, total: int, *, ordered: bool = False) -> list[int]:
    """Parse '1-3,5,7-' (1-based, inclusive) into 0-based indexes.

    Empty means every page. 'N-' runs to the last page. By default the result is
    sorted and de-duplicated; ordered=True keeps the order and repeats as written.
    """
    if total < 1:
        raise PdfToolError("The PDF has no pages.")
    if spec is None or not spec.strip():
        return list(range(total))
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:\s*(-)\s*(\d*))?", part)
        if not m:
            raise PdfToolError(
                f"Invalid page range '{part}'. Use numbers like '1-3,5,7-' "
                "(1-based; 'N-' means N to the last page)."
            )
        start = int(m.group(1))
        if m.group(2):
            end = int(m.group(3)) if m.group(3) else total
        else:
            end = start
        if start < 1 or end < 1:
            raise PdfToolError(f"Page numbers start at 1 (got '{part}').")
        if start > total or end > total:
            raise PdfToolError(f"Page range '{part}' is outside this PDF, which has {total} pages.")
        if end < start:
            raise PdfToolError(f"Page range '{part}' runs backwards.")
        out.extend(range(start - 1, end))
    if not out:
        raise PdfToolError("No pages selected.")
    return out if ordered else sorted(set(out))


def format_pages(indexes: list[int]) -> str:
    """Compress 0-based indexes into '1-3,5,7-9' (1-based)."""
    nums = sorted(set(i + 1 for i in indexes))
    parts: list[str] = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(parts)


# --------------------------------------------------------------------------
# pdf_info
# --------------------------------------------------------------------------

_PAPER = {
    (595, 842): "A4",
    (612, 792): "US Letter",
    (612, 1008): "US Legal",
    (842, 1191): "A3",
    (420, 595): "A5",
}


def _size_label(w: int, h: int) -> str:
    for (pw, ph), name in _PAPER.items():
        if abs(w - pw) <= 1 and abs(h - ph) <= 1:
            return name
        if abs(w - ph) <= 1 and abs(h - pw) <= 1:
            return f"{name} landscape"
    return ""


def _flatten_outline(
    reader: PdfReader, items: list[Any], depth: int, out: list[str], limit: int
) -> None:
    for item in items:
        if len(out) >= limit:
            return
        if isinstance(item, list):
            _flatten_outline(reader, item, depth + 1, out, limit)
        elif isinstance(item, Destination):
            try:
                num = reader.get_destination_page_number(item)
            except _PDF_ERRORS:
                num = None
            where = f" (page {num + 1})" if num is not None else " (page unknown)"
            out.append(f"{'  ' * depth}- {item.title}{where}")


def _text_sample(reader: PdfReader, total: int, n: int = 10) -> tuple[int, int]:
    """Return (pages_with_text, pages_checked) over up to n evenly spaced pages."""
    if total <= n:
        idx = list(range(total))
    else:
        idx = sorted({round(i * (total - 1) / (n - 1)) for i in range(n)})
    with_text = sum(1 for i in idx if (reader.pages[i].extract_text() or "").strip())
    return with_text, len(idx)


def pdf_info(sb: Sandbox, path: str, password: str | None = None) -> str:
    p = sb.input_pdf(path)
    size_kb = p.stat().st_size / 1024
    try:
        reader = open_reader(p, password)
    except PasswordRequired:
        return (
            f"File: {p}\nSize: {size_kb:.1f} KiB\nEncrypted: yes - a password is required "
            "to read anything else. Retry with the password parameter."
        )
    total = len(reader.pages)
    lines = [f"File: {p}", f"Size: {size_kb:.1f} KiB", f"Pages: {total}"]
    lines.append(
        "Encrypted: yes (opened with the given password)" if reader.is_encrypted else "Encrypted: no"
    )

    meta = reader.metadata
    shown = []
    if meta is not None:
        for label, value in (
            ("Title", meta.title),
            ("Author", meta.author),
            ("Subject", meta.subject),
            ("Creator", meta.creator),
            ("Producer", meta.producer),
            ("Created", meta.get("/CreationDate")),
            ("Modified", meta.get("/ModDate")),
        ):
            if value:
                shown.append(f"  {label}: {value}")
    lines.append("Metadata:" if shown else "Metadata: none")
    lines.extend(shown)

    with_text, checked = _text_sample(reader, total)
    if with_text == 0:
        lines.append(
            f"Text layer: NO text found on {checked} sampled page(s) - this looks like a scanned "
            "document. read_pdf and search_pdf will return nothing useful; OCR is needed."
        )
    elif with_text == checked:
        lines.append(f"Text layer: yes ({with_text}/{checked} sampled pages have extractable text)")
    else:
        lines.append(
            f"Text layer: partial ({with_text}/{checked} sampled pages have text; "
            "the rest may be scanned images)"
        )

    sizes: dict[tuple[int, int, int], list[int]] = {}
    for i, page in enumerate(reader.pages):
        box = page.mediabox
        key = (round(float(box.width)), round(float(box.height)), int(page.rotation or 0))
        sizes.setdefault(key, []).append(i)
    lines.append("Page sizes (points, 72 per inch):")
    for (w, h, rot), idx in sizes.items():
        extra = ", ".join(x for x in (_size_label(w, h), f"rotated {rot}" if rot else "") if x)
        lines.append(f"  {w} x {h}{f' ({extra})' if extra else ''}: pages {format_pages(idx)}")

    outline: list[str] = []
    _flatten_outline(reader, list(reader.outline), 0, outline, 200)
    if outline:
        lines.append("Outline / bookmarks:")
        lines.extend(f"  {o}" for o in outline)
        if len(outline) >= 200:
            lines.append("  ... (outline truncated at 200 entries)")
    else:
        lines.append("Outline / bookmarks: none")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Token-aware output assembly (read_pdf, extract_tables)
# --------------------------------------------------------------------------

NO_TEXT_NOTE = (
    "[No extractable text on this page: it is probably a scanned image. "
    "Text extraction cannot read it; OCR is required.]"
)


def _assemble(blocks: list[str], max_tokens: int) -> tuple[str, int, bool]:
    """Join blocks within a token budget.

    Returns (text, number_of_blocks_included, first_block_was_cut). At least part
    of the first block is always returned so every call makes progress.
    """
    budget = max_tokens * CHARS_PER_TOKEN
    parts: list[str] = []
    used = 0
    for n, block in enumerate(blocks):
        cost = len(block) + 2
        if used + cost > budget:
            if n == 0:
                return block[:budget].rstrip(), 1, True
            return "\n\n".join(parts), n, False
        parts.append(block)
        used += cost
    return "\n\n".join(parts), len(blocks), False


def _truncation_hint(remaining: list[int], cut: int | None, max_tokens: int) -> str:
    lines = []
    if cut is not None:
        lines.append(
            f"[Page {cut + 1} alone exceeds max_tokens={max_tokens} and was cut short. "
            "Raise max_tokens to read it whole.]"
        )
    if remaining:
        lines.append(
            f"[Output truncated at about {max_tokens} tokens. Continue with "
            f'pages="{format_pages(remaining)}" ({len(remaining)} more page(s), '
            f"starting at page {remaining[0] + 1}).]"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# read_pdf
# --------------------------------------------------------------------------


def read_pdf(
    sb: Sandbox,
    path: str,
    pages: str | None = None,
    layout: bool = False,
    max_tokens: int = 8000,
    password: str | None = None,
) -> str:
    if max_tokens < 1:
        raise PdfToolError("max_tokens must be at least 1.")
    p = sb.input_pdf(path)
    reader = open_reader(p, password)
    total = len(reader.pages)
    selected = parse_pages(pages, total)
    budget_chars = max_tokens * CHARS_PER_TOKEN

    blocks: list[str] = []
    done: list[int] = []
    empty: list[int] = []
    chars = 0
    for i in selected:
        try:
            text = reader.pages[i].extract_text(extraction_mode="layout" if layout else "plain")
        except _PDF_ERRORS as e:
            raise PdfToolError(f"Could not extract text from page {i + 1}: {e}") from e
        text = text.rstrip() if layout else text.strip()
        if not text.strip():
            empty.append(i)
            text = NO_TEXT_NOTE
        blocks.append(f"--- Page {i + 1} of {total} ---\n{text}")
        done.append(i)
        chars += len(blocks[-1]) + 2
        # Once the budget is blown, stop extracting: later pages are reported
        # as "not read yet" instead of being paid for. This keeps a 300-page
        # read cheap.
        if chars > budget_chars:
            break

    body, included, was_cut = _assemble(blocks, max_tokens)
    remaining = selected[included:]
    cut = done[0] if was_cut else None
    shown_empty = [i for i in empty if i in done[:included]]
    header = ""
    if shown_empty and len(shown_empty) == len(selected):
        header = (
            "WARNING: none of the selected pages has a text layer. This PDF looks scanned "
            "(images only); extracting text needs OCR, which this server does not do.\n\n"
        )
    elif shown_empty:
        header = (
            f"Note: page(s) {format_pages(shown_empty)} have no text layer "
            "(probably scanned images).\n\n"
        )
    hint = _truncation_hint(remaining, cut, max_tokens)
    return header + body + (("\n\n" + hint) if hint else "")


# --------------------------------------------------------------------------
# search_pdf
# --------------------------------------------------------------------------


def search_pdf(
    sb: Sandbox,
    path: str,
    query: str,
    pages: str | None = None,
    case_sensitive: bool = False,
    regex: bool = False,
    context_chars: int = 80,
    max_results: int = 30,
    password: str | None = None,
) -> str:
    if not query:
        raise PdfToolError("query must not be empty.")
    if max_results < 1 or context_chars < 0:
        raise PdfToolError("max_results must be >= 1 and context_chars >= 0.")
    if regex:
        pattern = query
    else:
        # Whitespace in the query matches any whitespace run, so a phrase still
        # matches when the PDF wraps it across lines.
        pattern = r"\s+".join(re.escape(w) for w in query.split()) or re.escape(query)
    try:
        rx = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
    except re.error as e:
        raise PdfToolError(f"Invalid regular expression: {e}") from e

    p = sb.input_pdf(path)
    reader = open_reader(p, password)
    selected = parse_pages(pages, len(reader.pages))

    results: list[str] = []
    match_count = 0
    pages_hit: list[int] = []
    no_text: list[int] = []
    for i in selected:
        try:
            text = reader.pages[i].extract_text() or ""
        except _PDF_ERRORS as e:
            raise PdfToolError(f"Could not extract text from page {i + 1}: {e}") from e
        if not text.strip():
            no_text.append(i)
            continue
        hit = False
        for m in rx.finditer(text):
            if m.end() == m.start():
                continue
            match_count += 1
            hit = True
            if len(results) < max_results:
                a = max(0, m.start() - context_chars)
                b = min(len(text), m.end() + context_chars)
                snippet = re.sub(r"\s+", " ", text[a:b]).strip()
                results.append(
                    f"Page {i + 1}: {'...' if a > 0 else ''}{snippet}{'...' if b < len(text) else ''}"
                )
        if hit:
            pages_hit.append(i)

    lines: list[str] = []
    if match_count == 0:
        lines.append(f"No matches for '{query}' in {len(selected) - len(no_text)} page(s) with text.")
    else:
        lines.append(
            f"{match_count} match(es) on {len(pages_hit)} page(s) ({format_pages(pages_hit)}):"
        )
        lines.extend(results)
        if match_count > len(results):
            lines.append(
                f"... {match_count - len(results)} more match(es) not shown; raise max_results "
                "or narrow pages."
            )
    if no_text:
        lines.append(
            f"Note: page(s) {format_pages(no_text)} have no text layer (probably scanned images) "
            "and could not be searched."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# extract_tables
# --------------------------------------------------------------------------


def _cell(v: Any) -> str:
    return "" if v is None else re.sub(r"\s+", " ", str(v)).strip()


def _markdown_table(rows: list[list[Any]]) -> str:
    width = max(len(r) for r in rows)
    cells = [[_cell(c).replace("|", "\\|") for c in r] + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(cells[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    out.extend("| " + " | ".join(r) + " |" for r in cells[1:])
    return "\n".join(out)


def _csv_table(rows: list[list[Any]]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows([[_cell(c) for c in r] for r in rows])
    return buf.getvalue().rstrip("\n")


def extract_tables(
    sb: Sandbox,
    path: str,
    pages: str | None = None,
    format: str = "markdown",
    strategy: str = "lines",
    max_tokens: int = 8000,
    password: str | None = None,
) -> str:
    if format not in ("markdown", "csv"):
        raise PdfToolError("format must be 'markdown' or 'csv'.")
    if strategy not in ("lines", "text"):
        raise PdfToolError("strategy must be 'lines' (ruled tables) or 'text' (borderless tables).")
    if max_tokens < 1:
        raise PdfToolError("max_tokens must be at least 1.")
    p = sb.input_pdf(path)
    reader = open_reader(p, password)
    selected = parse_pages(pages, len(reader.pages))
    settings = {"vertical_strategy": strategy, "horizontal_strategy": strategy}

    blocks: list[str] = []
    block_page: list[int] = []
    try:
        with pdfplumber.open(str(p), password=password or "") as pdf:
            for i in selected:
                raw_tables = pdf.pages[i].extract_tables(table_settings=settings)
                # The 'text' strategy emits all-blank rows between lines of words: drop them.
                tables = [t for t in ([r for r in tb if any(_cell(c) for c in r)] for tb in raw_tables) if t]
                for k, rows in enumerate(tables, 1):
                    shape = f"{len(rows)} rows x {max(len(r) for r in rows)} cols"
                    title = f"Table {k} on page {i + 1} ({shape})"
                    if format == "markdown":
                        blocks.append(f"### {title}\n\n{_markdown_table(rows)}")
                    else:
                        blocks.append(f"# {title}\n{_csv_table(rows)}")
                    block_page.append(i)
    except Exception as e:  # pdfminer raises many unrelated types; surface them clearly
        raise PdfToolError(f"Table extraction failed: {type(e).__name__}: {e}") from e

    if not blocks:
        hint = (
            "Try strategy='text' for tables without ruling lines. "
            if strategy == "lines"
            else "Tables without clearly aligned columns are not detected. "
        )
        return (
            f"No tables found on page(s) {format_pages(selected)}. {hint}"
            "If the pages are scanned images there is no text to read at all."
        )
    body, included, was_cut = _assemble(blocks, max_tokens)
    shown = set(block_page[:included])
    remaining = sorted({pg for pg in block_page[included:] if pg not in shown})
    cut = block_page[0] if was_cut else None
    summary = f"{len(blocks)} table(s) found on page(s) {format_pages(sorted(set(block_page)))}.\n\n"
    hint = _truncation_hint(remaining, cut, max_tokens)
    return summary + body + (("\n\n" + hint) if hint else "")


# --------------------------------------------------------------------------
# merge / split / extract / rotate
# --------------------------------------------------------------------------


def merge_pdfs(
    sb: Sandbox,
    paths: list[str],
    output_path: str | None = None,
    overwrite: bool = False,
    password: str | None = None,
) -> str:
    if len(paths) < 2:
        raise PdfToolError("Give at least two PDFs to merge, in the order they should appear.")
    ins = [sb.input_pdf(x) for x in paths]
    out = sb.output_pdf(output_path, ins[0].with_name("merged.pdf"))
    check_free(out, overwrite)
    writer = PdfWriter()
    total = 0
    for p in ins:
        reader = open_reader(p, password)
        total += len(reader.pages)
        try:
            writer.append(reader)
        except _PDF_ERRORS as e:
            raise PdfToolError(f"Could not merge {p.name}: {e}") from e
    write_pdf(writer, out, overwrite)
    return f"Merged {len(ins)} files ({total} pages) into {out}"


def split_pdf(
    sb: Sandbox,
    path: str,
    ranges: str,
    output_dir: str | None = None,
    overwrite: bool = False,
    password: str | None = None,
) -> str:
    if not ranges or not ranges.strip():
        raise PdfToolError("ranges is required, e.g. '1-3,5,7-' (each comma part becomes one file).")
    p = sb.input_pdf(path)
    reader = open_reader(p, password)
    total = len(reader.pages)
    out_dir = sb.output_dir(output_dir, p.parent)
    parts = [parse_pages(part, total, ordered=True) for part in ranges.split(",") if part.strip()]
    if not parts:
        raise PdfToolError("ranges is required, e.g. '1-3,5,7-' (each comma part becomes one file).")
    plan: list[tuple[Path, list[int]]] = []
    for k, idx in enumerate(parts, 1):
        label = f"{idx[0] + 1}" if len(idx) == 1 else f"{idx[0] + 1}-{idx[-1] + 1}"
        plan.append((out_dir / f"{p.stem}_part{k}_p{label}.pdf", idx))
    for target, _ in plan:  # check everything first so a conflict leaves nothing half-written
        check_free(target, overwrite)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for target, idx in plan:
        writer = PdfWriter()
        for i in idx:
            writer.add_page(reader.pages[i])
        write_pdf(writer, target, overwrite)
        lines.append(f"  {target.name}: {len(idx)} page(s)")
    return f"Split {p.name} ({total} pages) into {len(plan)} file(s) in {out_dir}:\n" + "\n".join(lines)


def extract_pages(
    sb: Sandbox,
    path: str,
    pages: str,
    output_path: str | None = None,
    overwrite: bool = False,
    password: str | None = None,
) -> str:
    if not pages or not pages.strip():
        raise PdfToolError("pages is required, e.g. '2-4,9'.")
    p = sb.input_pdf(path)
    reader = open_reader(p, password)
    selected = parse_pages(pages, len(reader.pages), ordered=True)
    out = sb.output_pdf(output_path, p.with_name(f"{p.stem}_extracted.pdf"))
    check_free(out, overwrite)
    writer = PdfWriter()
    for i in selected:
        writer.add_page(reader.pages[i])
    write_pdf(writer, out, overwrite)
    return f"Wrote {len(selected)} page(s) of {p.name} (pages {pages.strip()}) to {out}"


def rotate_pages(
    sb: Sandbox,
    path: str,
    degrees: int,
    pages: str | None = None,
    output_path: str | None = None,
    overwrite: bool = False,
    password: str | None = None,
) -> str:
    if degrees == 0 or degrees % 90 != 0:
        raise PdfToolError("degrees must be a non-zero multiple of 90 (90 = clockwise quarter turn).")
    p = sb.input_pdf(path)
    reader = open_reader(p, password)
    selected = parse_pages(pages, len(reader.pages))
    out = sb.output_pdf(output_path, p.with_name(f"{p.stem}_rotated.pdf"))
    check_free(out, overwrite)
    writer = PdfWriter()
    writer.append(reader)
    for i in selected:
        writer.pages[i].rotate(degrees)
    write_pdf(writer, out, overwrite)
    return (
        f"Rotated {len(selected)} page(s) ({format_pages(selected)}) by {degrees} degrees "
        f"clockwise; saved to {out}"
    )


# --------------------------------------------------------------------------
# extract_images
# --------------------------------------------------------------------------


def extract_images(
    sb: Sandbox,
    path: str,
    pages: str | None = None,
    output_dir: str | None = None,
    overwrite: bool = False,
    max_images: int = 200,
    password: str | None = None,
) -> str:
    if max_images < 1:
        raise PdfToolError("max_images must be at least 1.")
    p = sb.input_pdf(path)
    reader = open_reader(p, password)
    selected = parse_pages(pages, len(reader.pages))
    out_dir = sb.output_dir(output_dir, p.parent / f"{p.stem}_images")

    found: list[tuple[Path, bytes]] = []
    for i in selected:
        try:
            for k, img in enumerate(reader.pages[i].images, 1):
                ext = Path(img.name).suffix or ".bin"
                found.append((out_dir / f"{p.stem}_p{i + 1}_{k}{ext}", img.data))
        except (*_PDF_ERRORS, OSError) as e:
            raise PdfToolError(f"Could not extract images from page {i + 1}: {e}") from e
        if len(found) > max_images:
            raise PdfToolError(
                f"More than {max_images} images found (stopped at page {i + 1}). "
                "Narrow pages or raise max_images."
            )
    if not found:
        return f"No embedded images found on page(s) {format_pages(selected)}."
    for target, _ in found:
        check_free(target, overwrite)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for target, data in found:
        write_bytes(target, data, overwrite)
        lines.append(f"  {target.name} ({len(data) / 1024:.1f} KiB)")
    return f"Extracted {len(found)} image(s) to {out_dir}:\n" + "\n".join(lines)
