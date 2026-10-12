# Converts docs/fyp2/0X-*.md progress reports to separate PDFs.
# Walks markdown-it-py's FLAT token stream (open/close pairs + inline tokens).
# Usage: python scripts/md_to_pdf.py (outputs to docs/fyp2/pdf/)
import re
from pathlib import Path

from markdown_it import MarkdownIt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

BASE = ParagraphStyle(
    "base", fontName="Helvetica", fontSize=9.5, leading=13.5, spaceAfter=4
)
H1 = ParagraphStyle("h1", parent=BASE, fontSize=16, leading=20, spaceBefore=10, spaceAfter=8, textColor=colors.HexColor("#1d4fa0"))
H2 = ParagraphStyle("h2", parent=BASE, fontSize=12.5, leading=16, spaceBefore=10, spaceAfter=5, textColor=colors.HexColor("#1d4fa0"))
H3 = ParagraphStyle("h3", parent=BASE, fontSize=10.5, leading=14, spaceBefore=8, spaceAfter=4, textColor=colors.HexColor("#333333"))
CODE = ParagraphStyle(
    "code", parent=BASE, fontName="Courier", fontSize=8, leading=10.5,
    backColor=colors.HexColor("#f2f0ea"), borderPadding=4, spaceBefore=4, spaceAfter=6,
)
CELL = ParagraphStyle("cell", parent=BASE, fontSize=8, leading=11, spaceAfter=0)
CELL_HEAD = ParagraphStyle("cellhead", parent=CELL, textColor=colors.white, fontName="Helvetica-Bold")
LI = ParagraphStyle("li", parent=BASE, leftIndent=12, spaceAfter=2)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline(s: str) -> str:
    """Minimal inline markdown -> reportlab markup. Inline code is stashed in
    placeholders first so the italic/bold/link passes can never match across
    code-span boundaries (e.g. `*.a`, `*.b` must not pair into an <i>)."""
    s = esc(s)
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`]+)`", stash, s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"~~([^~]+)~~", r"<s>\1</s>", s)
    s = re.sub(r"(?<![\w\x00])\*([^*\n]+)\*(?![\w\x00])", r"<i>\1</i>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<link href="\2"><font color="#1d4fa0">\1</font></link>', s)

    def unstash(m: re.Match) -> str:
        return f'<font face="Courier" size="8.5">{codes[int(m.group(1))]}</font>'

    s = re.sub("\x00(\\d+)\x00", unstash, s)
    return s.replace("✅", "[OK]").replace("❌", "[X]")


def convert(md_path: Path, pdf_path: Path) -> None:
    md = MarkdownIt("commonmark").enable("table")
    tokens = md.parse(md_path.read_text(encoding="utf-8"))
    story = []

    i = 0
    heading_style = None
    in_list = None  # None | 'bullet' | 'ordered'
    list_n = 0
    in_table = False
    in_head_row = False
    table_rows = []  # list of list[str]; first row is header
    cur_row = None

    def flush_table():
        nonlocal table_rows
        if not table_rows:
            return
        ncols = max(len(r) for r in table_rows)
        data = []
        for ri, row in enumerate(table_rows):
            row = row + [""] * (ncols - len(row))
            style = CELL_HEAD if ri == 0 else CELL
            data.append([Paragraph(inline(c), style) for c in row])
        tbl = Table(data, repeatRows=1, hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d4fa0")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b9c6dd")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(KeepTogether([tbl, Spacer(1, 5)]))
        table_rows = []

    while i < len(tokens):
        t = tokens[i]
        ty, tag = t.type, t.tag
        if ty == "heading_open":
            heading_style = {"h1": H1, "h2": H2}.get(tag, H3)
        elif ty == "inline" and heading_style is not None:
            story.append(Paragraph(inline(t.content), heading_style))
            heading_style = None
        elif ty == "inline" and in_table:
            if cur_row is not None:
                cur_row.append(t.content)  # raw; converted once in flush_table
        elif ty == "inline" and in_list:
            marker = "•" if in_list == "bullet" else f"{list_n}."
            story.append(Paragraph(f"{marker} {inline(t.content)}", LI))
            list_n += 1
        elif ty == "inline":
            story.append(Paragraph(inline(t.content), BASE))
        elif ty == "bullet_list_open":
            in_list, list_n = "bullet", 0
        elif ty == "ordered_list_open":
            in_list, list_n = "ordered", 1
        elif ty in ("bullet_list_close", "ordered_list_close"):
            in_list = None
        elif ty == "fence" or ty == "code_block":
            story.append(Paragraph(esc(t.content).replace("\n", "<br/>"), CODE))
        elif ty == "table_open":
            in_table, table_rows, cur_row = True, [], None
        elif ty == "table_close":
            flush_table()
            in_table = False
        elif ty == "tr_open":
            cur_row = []
        elif ty == "tr_close":
            if cur_row is not None:
                table_rows.append(cur_row)
            cur_row = None
        elif ty == "hr":
            story.append(HRFlowable(width="100%", thickness=0.7, color=colors.HexColor("#c9c2b2"), spaceBefore=6, spaceAfter=6))
        i += 1

    doc = SimpleDocTemplate(
        str(pdf_path), pagesizes=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=md_path.stem,
    )
    doc.build(story)
    print("OK", pdf_path, f"({len(story)} flowables)")


if __name__ == "__main__":
    out_dir = Path("docs/fyp2/pdf")
    out_dir.mkdir(parents=True, exist_ok=True)
    for md in sorted(Path("docs/fyp2").glob("0*.md")):
        convert(md, out_dir / f"{md.stem}.pdf")
