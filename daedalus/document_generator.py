"""Daedalus Document Generator — генерира PDF и DOCX файлове от markdown съдържание.

Тригер маркер в LLM отговора:
  <NEED_DOCUMENT: pdf | Report Title | ## Markdown content here>
  <NEED_DOCUMENT: docx | Report Title | ## Markdown content here>

Файловете се записват в data/daedalus/documents/ и се сервират от:
  GET /admin/daedalus/documents/{filename}

Markdown поддръжка: заглавия (# ## ###), bold (**text**), italic (*text*),
bullet lists (- item), numbered lists (1. item), code blocks (```), хоризонтални линии (---).
"""
from __future__ import annotations
from .paths import data_root

import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DATA_ROOT = data_root()
DOCUMENTS_DIR = DATA_ROOT / "documents"
DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Markdown parser (lightweight, no external deps beyond reportlab/python-docx)
# ---------------------------------------------------------------------------

def _parse_markdown(text: str) -> list[dict[str, Any]]:
    """Parse markdown into a list of block dicts understood by both PDF and DOCX renderers."""
    blocks: list[dict[str, Any]] = []
    lines = text.splitlines()
    in_code = False
    code_lines: list[str] = []

    for line in lines:
        # Code block toggle
        if line.strip().startswith("```"):
            if in_code:
                blocks.append({"type": "code", "text": "\n".join(code_lines)})
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue

        # Headings
        m = re.match(r"^(#{1,3})\s+(.+)$", line)
        if m:
            level = len(m.group(1))
            blocks.append({"type": f"h{level}", "text": m.group(2).strip()})
            continue

        # Horizontal rule
        if re.match(r"^[-*_]{3,}\s*$", line.strip()):
            blocks.append({"type": "hr"})
            continue

        # Bullet list
        m = re.match(r"^[-*+]\s+(.+)$", line)
        if m:
            blocks.append({"type": "bullet", "text": m.group(1).strip()})
            continue

        # Numbered list
        m = re.match(r"^\d+\.\s+(.+)$", line)
        if m:
            blocks.append({"type": "numbered", "text": m.group(1).strip()})
            continue

        # Empty line
        if not line.strip():
            blocks.append({"type": "space"})
            continue

        # Normal paragraph
        blocks.append({"type": "para", "text": line.strip()})

    if in_code and code_lines:
        blocks.append({"type": "code", "text": "\n".join(code_lines)})

    return blocks


def _strip_inline(text: str) -> str:
    """Strip bold/italic markers for plain text usage."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text


# ---------------------------------------------------------------------------
# PDF generation (reportlab)
# ---------------------------------------------------------------------------

def _generate_pdf(title: str, content: str, author: str = "Daedalus / ADN") -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
        Preformatted, ListFlowable, ListItem,
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER

    filename = f"{uuid.uuid4().hex[:12]}_{int(time.time())}.pdf"
    filepath = DOCUMENTS_DIR / filename

    doc = SimpleDocTemplate(
        str(filepath),
        pagesize=A4,
        leftMargin=2.5 * cm, rightMargin=2.5 * cm,
        topMargin=2.5 * cm, bottomMargin=2.5 * cm,
        title=title, author=author,
    )

    styles = getSampleStyleSheet()
    dark = colors.HexColor("#0b1220")
    accent = colors.HexColor("#22d3ee")
    dim = colors.HexColor("#64748b")

    s_title = ParagraphStyle("DTitle", parent=styles["Title"],
                              fontSize=22, textColor=dark, spaceAfter=6)
    s_meta = ParagraphStyle("DMeta", parent=styles["Normal"],
                             fontSize=9, textColor=dim, spaceAfter=18)
    s_h1 = ParagraphStyle("DH1", parent=styles["Heading1"],
                           fontSize=16, textColor=dark, spaceBefore=14, spaceAfter=6)
    s_h2 = ParagraphStyle("DH2", parent=styles["Heading2"],
                           fontSize=13, textColor=dark, spaceBefore=10, spaceAfter=4)
    s_h3 = ParagraphStyle("DH3", parent=styles["Heading3"],
                           fontSize=11, textColor=dim, spaceBefore=8, spaceAfter=4)
    s_body = ParagraphStyle("DBody", parent=styles["Normal"],
                             fontSize=10, leading=15, spaceAfter=6)
    s_code = ParagraphStyle("DCode", parent=styles["Code"],
                             fontSize=8, leading=12,
                             backColor=colors.HexColor("#f1f5f9"),
                             leftIndent=12, rightIndent=12,
                             borderPadding=(6, 6, 6, 6),
                             spaceAfter=8)
    s_bullet = ParagraphStyle("DBullet", parent=styles["Normal"],
                               fontSize=10, leading=14,
                               leftIndent=20, firstLineIndent=-10, spaceAfter=3)

    def inline_para(text: str, style: ParagraphStyle) -> Paragraph:
        # Convert **bold** and *italic* to reportlab markup
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
        text = re.sub(r"`(.+?)`", r"<font face='Courier' size=9>\1</font>", text)
        # Escape & and < that aren't part of markup
        # (already handled by reportlab for <b> etc)
        return Paragraph(text, style)

    story = [
        Paragraph(title, s_title),
        Paragraph(f"Generated by Daedalus · ArgosDataNexus · {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}", s_meta),
        HRFlowable(width="100%", thickness=1, color=accent, spaceAfter=16),
    ]

    blocks = _parse_markdown(content)
    bullet_buffer: list[Any] = []

    def flush_bullets():
        if bullet_buffer:
            story.append(ListFlowable(
                [ListItem(Paragraph(_strip_inline(b["text"]), s_bullet), bulletColor=accent)
                 for b in bullet_buffer],
                bulletType="bullet", leftIndent=20, spaceAfter=8,
            ))
            bullet_buffer.clear()

    for block in blocks:
        t = block["type"]
        if t == "bullet":
            bullet_buffer.append(block)
            continue
        flush_bullets()

        if t == "h1":
            story.append(inline_para(block["text"], s_h1))
        elif t == "h2":
            story.append(inline_para(block["text"], s_h2))
        elif t == "h3":
            story.append(inline_para(block["text"], s_h3))
        elif t == "para":
            story.append(inline_para(block["text"], s_body))
        elif t == "numbered":
            story.append(Paragraph(f"  {_strip_inline(block['text'])}", s_bullet))
        elif t == "code":
            story.append(Preformatted(block["text"], s_code))
        elif t == "hr":
            story.append(HRFlowable(width="100%", thickness=0.5, color=dim, spaceAfter=10))
        elif t == "space":
            story.append(Spacer(1, 6))

    flush_bullets()
    doc.build(story)
    log.info("PDF generated: %s (%d bytes)", filename, filepath.stat().st_size)
    return filepath


# ---------------------------------------------------------------------------
# DOCX generation (python-docx)
# ---------------------------------------------------------------------------

def _generate_docx(title: str, content: str, author: str = "Daedalus / ADN") -> Path:
    from docx import Document
    from docx.shared import Pt, RGBColor, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    filename = f"{uuid.uuid4().hex[:12]}_{int(time.time())}.docx"
    filepath = DOCUMENTS_DIR / filename

    doc = Document()

    # Page margins
    for section in doc.sections:
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(3)
        section.right_margin = Cm(3)

    DARK = RGBColor(0x0b, 0x12, 0x20)
    ACCENT = RGBColor(0x22, 0xd3, 0xee)
    DIM = RGBColor(0x64, 0x74, 0x8b)

    # Title
    t_para = doc.add_heading(title, 0)
    t_para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    for run in t_para.runs:
        run.font.color.rgb = DARK
        run.font.size = Pt(22)

    meta = doc.add_paragraph(
        f"Generated by Daedalus · ArgosDataNexus · {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"
    )
    meta.runs[0].font.size = Pt(8)
    meta.runs[0].font.color.rgb = DIM

    doc.add_paragraph("—" * 60)

    def add_inline_run(para, text: str, bold=False, italic=False, code=False):
        """Add a run with optional inline formatting."""
        run = para.add_run(text)
        if bold:
            run.bold = True
        if italic:
            run.italic = True
        if code:
            run.font.name = "Courier New"
            run.font.size = Pt(9)
        return run

    def add_formatted_para(para, text: str, base_size: int = 10):
        """Parse **bold**, *italic*, `code` inline markup into runs."""
        pattern = re.compile(r"(\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`)")
        last = 0
        for m in pattern.finditer(text):
            if m.start() > last:
                add_inline_run(para, text[last:m.start()])
            if m.group(0).startswith("**"):
                add_inline_run(para, m.group(2), bold=True)
            elif m.group(0).startswith("*"):
                add_inline_run(para, m.group(3), italic=True)
            else:
                add_inline_run(para, m.group(4), code=True)
            last = m.end()
        if last < len(text):
            add_inline_run(para, text[last:])

    blocks = _parse_markdown(content)
    for block in blocks:
        t = block["type"]
        if t in ("h1", "h2", "h3"):
            level = int(t[1])
            h = doc.add_heading(_strip_inline(block["text"]), level=level)
            for run in h.runs:
                run.font.color.rgb = DARK
        elif t == "para":
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(6)
            add_formatted_para(p, block["text"])
        elif t == "bullet":
            p = doc.add_paragraph(style="List Bullet")
            add_formatted_para(p, block["text"])
        elif t == "numbered":
            p = doc.add_paragraph(style="List Number")
            add_formatted_para(p, block["text"])
        elif t == "code":
            p = doc.add_paragraph()
            run = p.add_run(block["text"])
            run.font.name = "Courier New"
            run.font.size = Pt(8)
            p.paragraph_format.left_indent = Cm(1)
        elif t == "hr":
            doc.add_paragraph("—" * 60)
        elif t == "space":
            doc.add_paragraph("")

    doc.save(str(filepath))
    log.info("DOCX generated: %s (%d bytes)", filename, filepath.stat().st_size)
    return filepath


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_document(format_title_content: str) -> dict[str, Any]:
    """Parse the marker value and generate the document.

    ``format_title_content`` is: ``pdf | Title | ## Markdown content``
    or                           ``docx | Title | ## Markdown content``
    """
    parts = format_title_content.split("|", 2)
    if len(parts) < 3:
        return {"ok": False, "error": "Format: pdf|Title|## markdown content"}

    fmt = parts[0].strip().lower()
    title = parts[1].strip() or "Untitled Document"
    content = parts[2].strip()

    if not content:
        return {"ok": False, "error": "Document content is empty"}

    if fmt not in ("pdf", "docx"):
        return {"ok": False, "error": f"Unsupported format: {fmt!r}. Use 'pdf' or 'docx'."}

    try:
        if fmt == "pdf":
            filepath = _generate_pdf(title, content)
        else:
            filepath = _generate_docx(title, content)

        return {
            "ok": True,
            "format": fmt,
            "title": title,
            "filename": filepath.name,
            "size_bytes": filepath.stat().st_size,
            "download_url": f"/admin/daedalus/documents/{filepath.name}",
        }
    except Exception as exc:
        log.error("document_generator.generate_document failed: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc), "format": fmt}


def list_documents() -> list[dict[str, Any]]:
    docs = []
    for f in sorted(DOCUMENTS_DIR.glob("*.*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.suffix in (".pdf", ".docx"):
            docs.append({
                "filename": f.name,
                "format": f.suffix.lstrip("."),
                "size_bytes": f.stat().st_size,
                "created_at": f.stat().st_mtime,
                "download_url": f"/admin/daedalus/documents/{f.name}",
            })
    return docs
