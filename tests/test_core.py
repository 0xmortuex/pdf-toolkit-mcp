"""Every tool against real generated PDFs (no mocks)."""

from __future__ import annotations

import csv
import io
import time
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from pdf_toolkit_mcp import core
from pdf_toolkit_mcp.core import PdfToolError, Sandbox, parse_pages

# ---------------------------------------------------------------- page specs


def test_parse_pages_forms():
    assert parse_pages("", 10) == list(range(10))
    assert parse_pages("1-3,5,7-", 9) == [0, 1, 2, 4, 6, 7, 8]
    assert parse_pages("3,1,3", 5) == [0, 2]
    assert parse_pages("3,1,3", 5, ordered=True) == [2, 0, 2]
    assert parse_pages(" 2 - 4 ", 5) == [1, 2, 3]


@pytest.mark.parametrize("spec", ["0", "6", "2-9", "4-2", "a", "1--3", "-3", "1;2"])
def test_parse_pages_rejects(spec):
    with pytest.raises(PdfToolError):
        parse_pages(spec, 5)


# --------------------------------------------------------------------- info


def test_info_text_pdf(sb, text_pdf):
    out = core.pdf_info(sb, "doc.pdf")
    assert "Pages: 5" in out
    assert "Title: Test Document" in out and "Author: Fadi Tester" in out
    assert "Encrypted: no" in out
    assert "Text layer: yes" in out
    assert "612 x 792 (US Letter): pages 1-5" in out
    assert "- Chapter 1 (page 1)" in out
    assert "  - Section 2.1 (page 2)" in out  # nested one level deeper


def test_info_page_sizes_group(sb, work, text_pdf, landscape_pdf):
    w = PdfWriter()
    w.append(PdfReader(str(text_pdf)))
    w.append(PdfReader(str(landscape_pdf)))
    w.write(str(work / "m.pdf"))
    out = core.pdf_info(sb, "m.pdf")
    assert "pages 1-5" in out and "842 x 595 (A4 landscape): pages 6" in out


def test_info_scanned_says_so(sb, scanned_pdf):
    out = core.pdf_info(sb, str(scanned_pdf))
    assert "NO text found" in out and "OCR" in out


def test_info_encrypted(sb, encrypted_pdf):
    locked = core.pdf_info(sb, str(encrypted_pdf))
    assert "Encrypted: yes" in locked and "password is required" in locked
    assert "Pages:" not in locked
    opened = core.pdf_info(sb, str(encrypted_pdf), "s3cret-pw")
    assert "Pages: 5" in opened
    with pytest.raises(PdfToolError, match="wrong"):
        core.pdf_info(sb, str(encrypted_pdf), "nope")


def test_not_a_pdf(sb, work):
    (work / "fake.pdf").write_text("this is not a pdf", encoding="utf-8")
    with pytest.raises(PdfToolError, match="could not be read"):
        core.pdf_info(sb, "fake.pdf")
    with pytest.raises(PdfToolError, match="File not found"):
        core.pdf_info(sb, "missing.pdf")


# --------------------------------------------------------------------- read


def test_read_all_and_turkish(sb, text_pdf):
    out = core.read_pdf(sb, "doc.pdf")
    assert "--- Page 1 of 5 ---" in out and "--- Page 5 of 5 ---" in out
    assert "zebra3" in out
    assert "Çalışma: İstanbul'da öğrenci şarkısı, ğüşıöç ĞÜŞİÖÇ" in out
    assert "truncated" not in out


def test_read_page_range(sb, text_pdf):
    out = core.read_pdf(sb, "doc.pdf", pages="2,4-")
    assert "zebra2" in out and "zebra4" in out and "zebra5" in out
    assert "zebra1" not in out and "zebra3" not in out


def test_read_layout_mode(sb, text_pdf):
    out = core.read_pdf(sb, "doc.pdf", pages="1", layout=True)
    assert "Chapter 1 heading" in out and "zebra1" in out


def test_read_truncation_continues(big_pdf):
    sbig = Sandbox([big_pdf.parent])
    out = core.read_pdf(sbig, "big.pdf", max_tokens=2000)
    assert "--- Page 1 of 300 ---" in out
    assert 'Continue with pages="' in out
    start = int(out.rsplit("starting at page ", 1)[1].split(")")[0])
    assert 1 < start < 300
    assert f"--- Page {start - 1} of 300 ---" in out and f"--- Page {start} of 300 ---" not in out
    # Following the hint reads exactly the next page.
    hint = out.rsplit('pages="', 1)[1].split('"')[0]
    assert hint == f"{start}-300"
    nxt = core.read_pdf(sbig, "big.pdf", pages=hint, max_tokens=2000)
    assert f"--- Page {start} of 300 ---" in nxt
    assert len(out) < 2000 * 4 + 500


def test_read_truncation_respects_subset(big_pdf):
    out = core.read_pdf(Sandbox([big_pdf.parent]), "big.pdf", pages="1,3,5", max_tokens=1000)
    assert 'pages="3,5"' in out or 'pages="5"' in out
    assert 'pages="2' not in out


def test_read_single_page_exceeding_budget(big_pdf):
    out = core.read_pdf(Sandbox([big_pdf.parent]), "big.pdf", pages="1-2", max_tokens=50)
    assert "alone exceeds" in out and 'pages="2"' in out


def test_read_scanned_pages_flagged(sb, scanned_pdf, mixed_pdf):
    out = core.read_pdf(sb, str(scanned_pdf))
    assert out.startswith("WARNING: none of the selected pages has a text layer")
    assert "OCR" in out and "--- Page 1 of 2 ---" in out
    mixed = core.read_pdf(sb, str(mixed_pdf))
    assert "Note: page(s) 3-4 have no text layer" in mixed
    assert "zebra1" in mixed and "zebra3" in mixed


def test_read_encrypted(sb, encrypted_pdf):
    with pytest.raises(PdfToolError, match="encrypted"):
        core.read_pdf(sb, str(encrypted_pdf))
    with pytest.raises(PdfToolError, match="password .* wrong"):
        core.read_pdf(sb, str(encrypted_pdf), password="bad")
    assert "zebra1" in core.read_pdf(sb, str(encrypted_pdf), password="s3cret-pw")


def test_password_never_in_errors(sb, encrypted_pdf):
    secret = "totally-wrong-pw-123"
    with pytest.raises(PdfToolError) as ei:
        core.read_pdf(sb, str(encrypted_pdf), password=secret)
    assert secret not in str(ei.value)


def test_empty_user_password_opens_without_one(sb, work, text_pdf):
    w = PdfWriter(clone_from=PdfReader(str(text_pdf)))
    w.encrypt("", owner_password="owner", algorithm="AES-128")
    w.write(str(work / "ownerlocked.pdf"))
    assert "zebra1" in core.read_pdf(sb, "ownerlocked.pdf")


# ------------------------------------------------------------------- search


def test_search_basic(sb, text_pdf):
    out = core.search_pdf(sb, "doc.pdf", "zebra3")
    assert "1 match(es) on 1 page(s) (3)" in out
    assert "Page 3:" in out and "marker word zebra3 and some text" in out


def test_search_case_and_turkish(sb, text_pdf):
    assert "5 match(es)" in core.search_pdf(sb, "doc.pdf", "chapter")
    assert "No matches" in core.search_pdf(sb, "doc.pdf", "CHAPTER", case_sensitive=True)
    assert "5 match(es)" in core.search_pdf(sb, "doc.pdf", "öğrenci şarkısı")
    # dotted/dotless I fold correctly in both directions
    assert "5 match(es)" in core.search_pdf(sb, "doc.pdf", "istanbul'da")
    assert "5 match(es)" in core.search_pdf(sb, "doc.pdf", "ÖĞRENCI ŞARKISI")


def test_search_phrase_across_lines(sb, text_pdf):
    assert "5 match(es)" in core.search_pdf(sb, "doc.pdf", "split across two lines")


def test_search_regex_pages_and_cap(sb, text_pdf):
    out = core.search_pdf(sb, "doc.pdf", r"zebra[24]\b", regex=True)
    assert "2 match(es) on 2 page(s) (2,4)" in out
    out = core.search_pdf(sb, "doc.pdf", "zebra", pages="3-5", max_results=1)
    assert "3 match(es)" in out and "2 more match(es) not shown" in out
    with pytest.raises(PdfToolError, match="Invalid regular expression"):
        core.search_pdf(sb, "doc.pdf", "(", regex=True)


def test_search_notes_unsearchable_pages(sb, mixed_pdf):
    out = core.search_pdf(sb, str(mixed_pdf), "zebra")
    assert "2 match(es)" in out and "page(s) 3-4 have no text layer" in out


def test_search_big_finds_needle(big_pdf):
    out = core.search_pdf(Sandbox([big_pdf.parent]), "big.pdf", "NEEDLE-IN-HAYSTACK")
    assert "1 match(es) on 1 page(s) (250)" in out


# ------------------------------------------------------------------- tables


def test_tables_markdown(sb, table_pdf):
    out = core.extract_tables(sb, "tables.pdf")
    assert "1 table(s) found on page(s) 1." in out
    assert "### Table 1 on page 1 (4 rows x 3 cols)" in out
    assert "| Item | Qty | Price |" in out and "| --- | --- | --- |" in out
    assert "| Widget | 4 | 9.50 |" in out
    assert "| Gadget \\| Pro | 10 | 120.00 |" in out  # pipe escaped
    assert "| Şeker (sugar) | 2 | 3.75 |" in out


def test_tables_csv(sb, table_pdf):
    out = core.extract_tables(sb, "tables.pdf", format="csv")
    body = out.split("# Table 1", 1)[1].split("\n", 1)[1]
    rows = list(csv.reader(io.StringIO(body)))
    assert rows[0] == ["Item", "Qty", "Price"]
    assert rows[2] == ["Gadget | Pro", "10", "120.00"]


def test_tables_none_found_and_borderless(sb, text_pdf, borderless_pdf):
    out = core.extract_tables(sb, "doc.pdf")
    assert "No tables found" in out and "strategy='text'" in out
    ruled = core.extract_tables(sb, "borderless.pdf")
    assert "No tables found" in ruled
    out = core.extract_tables(sb, "borderless.pdf", strategy="text")
    assert "| Name | Score | Grade |" in out and "| Linus | 88 | B |" in out
    assert "(4 rows x 3 cols)" in out and "|  |  |  |" not in out  # blank spacer rows dropped


def test_tables_bad_args_and_password(sb, table_pdf, encrypted_pdf):
    with pytest.raises(PdfToolError, match="format"):
        core.extract_tables(sb, "tables.pdf", format="xml")
    with pytest.raises(PdfToolError, match="encrypted"):
        core.extract_tables(sb, str(encrypted_pdf))
    assert "No tables found" in core.extract_tables(sb, str(encrypted_pdf), password="s3cret-pw")


# -------------------------------------------------------------- page output


def pages_of(p: Path) -> int:
    return len(PdfReader(str(p)).pages)


def test_merge(sb, work, text_pdf, second_pdf):
    msg = core.merge_pdfs(sb, ["doc.pdf", "other.pdf"])
    out = work / "merged.pdf"
    assert str(out) in msg and "7 pages" in msg
    r = PdfReader(str(out))
    assert len(r.pages) == 7
    assert "quokka1" in r.pages[5].extract_text()
    titles = [d.title for d in r.outline if not isinstance(d, list)]
    assert "Chapter 1" in titles  # bookmarks survive


def test_merge_validation(sb, work, text_pdf, second_pdf):
    with pytest.raises(PdfToolError, match="at least two"):
        core.merge_pdfs(sb, ["doc.pdf"])
    core.merge_pdfs(sb, ["doc.pdf", "other.pdf"], output_path="x.pdf")
    with pytest.raises(PdfToolError, match="Refusing to overwrite"):
        core.merge_pdfs(sb, ["doc.pdf", "other.pdf"], output_path="x.pdf")
    core.merge_pdfs(sb, ["other.pdf", "doc.pdf"], output_path="x.pdf", overwrite=True)
    assert "quokka1" in PdfReader(str(work / "x.pdf")).pages[0].extract_text()
    with pytest.raises(PdfToolError, match=r"\.pdf"):
        core.merge_pdfs(sb, ["doc.pdf", "other.pdf"], output_path="x.txt")


def test_merge_encrypted_input(sb, work, encrypted_pdf, second_pdf):
    with pytest.raises(PdfToolError, match="encrypted"):
        core.merge_pdfs(sb, [str(encrypted_pdf), "other.pdf"])
    core.merge_pdfs(sb, [str(encrypted_pdf), "other.pdf"], output_path="mm.pdf", password="s3cret-pw")
    r = PdfReader(str(work / "mm.pdf"))
    assert not r.is_encrypted and len(r.pages) == 7


def test_split(sb, work, text_pdf):
    msg = core.split_pdf(sb, "doc.pdf", "1-2,4,5-")
    names = sorted(p.name for p in work.glob("doc_part*.pdf"))
    assert names == ["doc_part1_p1-2.pdf", "doc_part2_p4.pdf", "doc_part3_p5.pdf"]
    assert "3 file(s)" in msg
    assert [pages_of(work / n) for n in names] == [2, 1, 1]
    assert "zebra4" in PdfReader(str(work / "doc_part2_p4.pdf")).pages[0].extract_text()
    assert pages_of(work / "doc.pdf") == 5  # original untouched


def test_split_open_ended_and_outdir(sb, work, text_pdf):
    core.split_pdf(sb, "doc.pdf", "3-", output_dir="out/sub")
    f = work / "out" / "sub" / "doc_part1_p3-5.pdf"
    assert f.exists() and pages_of(f) == 3


def test_split_conflict_is_all_or_nothing(sb, work, text_pdf):
    (work / "doc_part2_p4.pdf").write_bytes(b"precious")
    with pytest.raises(PdfToolError, match="Refusing to overwrite"):
        core.split_pdf(sb, "doc.pdf", "1-2,4,5")
    assert not (work / "doc_part1_p1-2.pdf").exists()
    assert (work / "doc_part2_p4.pdf").read_bytes() == b"precious"


def test_split_bad_ranges(sb, text_pdf):
    with pytest.raises(PdfToolError, match="outside this PDF"):
        core.split_pdf(sb, "doc.pdf", "1-9")
    with pytest.raises(PdfToolError, match="required"):
        core.split_pdf(sb, "doc.pdf", " ")


def test_extract_pages_order(sb, work, text_pdf):
    msg = core.extract_pages(sb, "doc.pdf", "4,2,2")
    out = work / "doc_extracted.pdf"
    assert str(out) in msg
    r = PdfReader(str(out))
    assert len(r.pages) == 3
    for i, n in enumerate((4, 2, 2)):
        assert f"zebra{n}" in r.pages[i].extract_text()


def test_extract_pages_no_overwrite(sb, work, text_pdf):
    core.extract_pages(sb, "doc.pdf", "1")
    before = (work / "doc_extracted.pdf").read_bytes()
    with pytest.raises(PdfToolError, match="Refusing to overwrite"):
        core.extract_pages(sb, "doc.pdf", "2")
    assert (work / "doc_extracted.pdf").read_bytes() == before
    core.extract_pages(sb, "doc.pdf", "2", overwrite=True)
    assert "zebra2" in PdfReader(str(work / "doc_extracted.pdf")).pages[0].extract_text()


def test_rotate(sb, work, text_pdf):
    msg = core.rotate_pages(sb, "doc.pdf", 90, pages="2-3")
    r = PdfReader(str(work / "doc_rotated.pdf"))
    assert [p.rotation for p in r.pages] == [0, 90, 90, 0, 0]
    assert "2-3" in msg
    core.rotate_pages(sb, "doc_rotated.pdf", -90, pages="2", output_path="r2.pdf")
    assert [p.rotation for p in PdfReader(str(work / "r2.pdf")).pages] == [0, 0, 90, 0, 0]
    with pytest.raises(PdfToolError, match="multiple of 90"):
        core.rotate_pages(sb, "doc.pdf", 45)


def test_rotate_in_place_with_overwrite(sb, work, text_pdf):
    core.rotate_pages(sb, "doc.pdf", 180, output_path="doc.pdf", overwrite=True)
    r = PdfReader(str(work / "doc.pdf"))
    assert len(r.pages) == 5 and all(p.rotation == 180 for p in r.pages)
    assert not list(work.glob("*.tmp"))
    with pytest.raises(PdfToolError, match="Refusing to overwrite"):
        core.rotate_pages(sb, "doc.pdf", 90, output_path="doc.pdf")


# ------------------------------------------------------------------- images


def test_extract_images(sb, work, scanned_pdf):
    msg = core.extract_images(sb, "scanned.pdf")
    imgs = sorted((work / "scanned_images").iterdir())
    assert [i.name[:12] for i in imgs] == ["scanned_p1_1", "scanned_p2_1"]
    assert "2 image(s)" in msg
    with Image.open(imgs[0]) as im:
        assert im.size == (200, 100)
    with pytest.raises(PdfToolError, match="Refusing to overwrite"):
        core.extract_images(sb, "scanned.pdf")
    core.extract_images(sb, "scanned.pdf", pages="1", output_dir="o2")
    assert len(list((work / "o2").iterdir())) == 1


def test_extract_images_none_and_cap(sb, work, text_pdf, scanned_pdf):
    assert "No embedded images" in core.extract_images(sb, "doc.pdf")
    assert not (work / "doc_images").exists()
    with pytest.raises(PdfToolError, match="More than 1 images"):
        core.extract_images(sb, "scanned.pdf", max_images=1)
    assert not (work / "scanned_images").exists()


# ------------------------------------------------------------------ sandbox


def test_sandbox_blocks_outside(work, tmp_path_factory, text_pdf):
    inside = work / "in"
    inside.mkdir()
    other = tmp_path_factory.mktemp("elsewhere") / "x.pdf"
    other.write_bytes(text_pdf.read_bytes())
    sb = Sandbox([inside])
    for bad in (str(other), "../doc.pdf", str(inside / ".." / "doc.pdf")):
        with pytest.raises(PdfToolError, match="outside the allowed"):
            core.pdf_info(sb, bad)
    # writes are restricted too
    with pytest.raises(PdfToolError, match="outside the allowed"):
        core.extract_pages(Sandbox([work]), "doc.pdf", "1", output_path=str(other.with_name("y.pdf")))
    assert not other.with_name("y.pdf").exists()
    # a sibling folder sharing a name prefix must not pass
    sib = work / "in-evil"
    sib.mkdir()
    (sib / "a.pdf").write_bytes(text_pdf.read_bytes())
    with pytest.raises(PdfToolError, match="outside the allowed"):
        core.pdf_info(sb, str(sib / "a.pdf"))


def test_sandbox_multiple_dirs_and_relative(work, tmp_path_factory, text_pdf):
    d2 = tmp_path_factory.mktemp("second")
    (d2 / "b.pdf").write_bytes(text_pdf.read_bytes())
    sb = Sandbox([work, d2])
    assert "Pages: 5" in core.pdf_info(sb, "doc.pdf")  # relative -> first dir
    assert "Pages: 5" in core.pdf_info(sb, str(d2 / "b.pdf"))


def test_sandbox_symlink_escape(work, tmp_path_factory, text_pdf):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "s.pdf").write_bytes(text_pdf.read_bytes())
    inside = work / "box"
    inside.mkdir()
    try:
        (inside / "link.pdf").symlink_to(outside / "s.pdf")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this system")
    with pytest.raises(PdfToolError, match="outside the allowed"):
        core.pdf_info(Sandbox([inside]), "link.pdf")


def test_sandbox_missing_dir(work):
    with pytest.raises(PdfToolError, match="does not exist"):
        Sandbox([work / "nope"])


# -------------------------------------------------------------- performance


def test_big_pdf_performance(big_pdf):
    sbig = Sandbox([big_pdf.parent])
    t = time.perf_counter()
    info = core.pdf_info(sbig, "big.pdf")
    t_info = time.perf_counter() - t
    assert "Pages: 300" in info

    t = time.perf_counter()
    out = core.read_pdf(sbig, "big.pdf", max_tokens=8000)
    t_read = time.perf_counter() - t
    assert "Continue with" in out

    last = core.read_pdf(sbig, "big.pdf", pages="300", max_tokens=8000)
    assert "Page 300 line 54" in last

    t = time.perf_counter()
    core.search_pdf(sbig, "big.pdf", "NEEDLE-IN-HAYSTACK")
    t_search = time.perf_counter() - t

    t = time.perf_counter()
    core.split_pdf(sbig, "big.pdf", "1-100,101-300", output_dir="parts")
    t_split = time.perf_counter() - t
    print(f"info {t_info:.2f}s read {t_read:.2f}s search-all-300 {t_search:.2f}s split {t_split:.2f}s")
    # generous bounds: these catch accidental O(n^2) behaviour, not slow CI machines
    assert t_info < 30 and t_read < 30 and t_search < 120 and t_split < 60
