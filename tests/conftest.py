"""Real PDFs generated with reportlab (tests only) and pypdf."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import reportlab
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape, letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from pdf_toolkit_mcp.core import Sandbox

VERA = os.path.join(os.path.dirname(reportlab.__file__), "fonts", "Vera.ttf")
pdfmetrics.registerFont(TTFont("Vera", VERA))

TURKISH = "Çalışma: İstanbul'da öğrenci şarkısı, ğüşıöç ĞÜŞİÖÇ"


def make_text_pdf(path: Path, pages: int = 5) -> Path:
    """Pages with a unique marker word each, Turkish text, bookmarks and metadata."""
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setTitle("Test Document")
    c.setAuthor("Fadi Tester")
    for n in range(1, pages + 1):
        c.setFont("Vera", 14)
        c.drawString(72, 720, f"Chapter {n} heading")
        c.setFont("Vera", 11)
        c.drawString(72, 690, f"This page contains the marker word zebra{n} and some text.")
        c.drawString(72, 670, TURKISH)
        c.drawString(72, 650, "A phrase that is split")
        c.drawString(72, 636, "across two lines here.")
        c.bookmarkPage(f"p{n}")
        c.addOutlineEntry(f"Chapter {n}", f"p{n}", level=0)
        if n == 2:
            c.addOutlineEntry("Section 2.1", f"p{n}", level=1)
        c.showPage()
    c.save()
    return path


@pytest.fixture
def work(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def sb(work: Path) -> Sandbox:
    return Sandbox([work])


@pytest.fixture
def text_pdf(work: Path) -> Path:
    return make_text_pdf(work / "doc.pdf")


@pytest.fixture
def second_pdf(work: Path) -> Path:
    c = canvas.Canvas(str(work / "other.pdf"), pagesize=A4)
    for n in range(1, 3):
        c.setFont("Vera", 12)
        c.drawString(72, 750, f"Other document page {n} quokka{n}")
        c.showPage()
    c.save()
    return work / "other.pdf"


@pytest.fixture
def table_pdf(work: Path) -> Path:
    out = work / "tables.pdf"
    doc = SimpleDocTemplate(str(out), pagesize=letter)
    data = [
        ["Item", "Qty", "Price"],
        ["Widget", "4", "9.50"],
        ["Gadget | Pro", "10", "120.00"],
        ["Şeker (sugar)", "2", "3.75"],
    ]
    t = Table(data)
    t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), "Vera"),
            ]
        )
    )
    doc.build([t])
    return out


@pytest.fixture
def borderless_pdf(work: Path) -> Path:
    c = canvas.Canvas(str(work / "borderless.pdf"), pagesize=letter)
    c.setFont("Vera", 11)
    rows = [("Name", "Score", "Grade"), ("Ada", "95", "A"), ("Linus", "88", "B"), ("Grace", "91", "A")]
    for r, row in enumerate(rows):
        for col, cell in enumerate(row):
            c.drawString(72 + col * 120, 700 - r * 20, cell)
    c.save()
    return work / "borderless.pdf"


@pytest.fixture
def encrypted_pdf(work: Path, text_pdf: Path) -> Path:
    w = PdfWriter(clone_from=PdfReader(str(text_pdf)))
    w.encrypt("s3cret-pw", algorithm="AES-256")
    out = work / "locked.pdf"
    with open(out, "wb") as f:
        w.write(f)
    return out


@pytest.fixture
def scanned_pdf(work: Path) -> Path:
    """Two image-only pages (no text layer): what a scanner produces."""
    img = work / "scan.png"
    Image.effect_noise((200, 100), 60).convert("RGB").save(img)
    c = canvas.Canvas(str(work / "scanned.pdf"), pagesize=letter)
    for _ in range(2):
        c.drawImage(str(img), 72, 500, width=400, height=200)
        c.showPage()
    c.save()
    img.unlink()
    return work / "scanned.pdf"


@pytest.fixture
def mixed_pdf(work: Path, text_pdf: Path, scanned_pdf: Path) -> Path:
    w = PdfWriter()
    w.append(PdfReader(str(text_pdf)), pages=[0, 2])
    w.append(PdfReader(str(scanned_pdf)), pages=[0, 1])
    out = work / "mixed.pdf"
    with open(out, "wb") as f:
        w.write(f)
    return out


@pytest.fixture
def landscape_pdf(work: Path) -> Path:
    c = canvas.Canvas(str(work / "land.pdf"), pagesize=landscape(A4))
    c.drawString(72, 500, "wide")
    c.showPage()
    c.save()
    return work / "land.pdf"


@pytest.fixture(scope="session")
def big_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """300 pages of dense text."""
    out = tmp_path_factory.mktemp("big") / "big.pdf"
    c = canvas.Canvas(str(out), pagesize=letter)
    c.setFont("Vera", 9)
    for n in range(1, 301):
        for line in range(55):
            c.drawString(
                50, 760 - line * 13, f"Page {n} line {line}: the quick brown fox jumps over the lazy dog 0123456789"
            )
        if n == 250:
            c.drawString(50, 30, "NEEDLE-IN-HAYSTACK")
        c.showPage()
    c.save()
    return out
