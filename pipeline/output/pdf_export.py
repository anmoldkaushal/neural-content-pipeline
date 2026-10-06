"""Renders a finished job package as a clean, real-text PDF -- the deliverable a human actually
reads and copies from, instead of a terminal dump or raw package.json. Plain left-aligned
paragraphs only (no tables, no justified text, no multi-column layout) so copy-pasting a
paragraph out of the PDF doesn't pick up layout artifacts."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from pipeline.schemas import JobRecord

def field_label(field: str) -> str:
    return "CTA" if field == "cta" else field.replace("_", " ").capitalize()


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "doc_title": ParagraphStyle(
            "doc_title", parent=base["Title"], alignment=TA_LEFT, fontSize=18, spaceAfter=4
        ),
        "meta": ParagraphStyle(
            "meta", parent=base["Normal"], alignment=TA_LEFT, textColor=colors.grey, fontSize=9, spaceAfter=16
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], alignment=TA_LEFT, spaceBefore=14, spaceAfter=6
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"], alignment=TA_LEFT, spaceBefore=8, spaceAfter=2
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], alignment=TA_LEFT, fontSize=11, leading=15, spaceAfter=8
        ),
        "candidate": ParagraphStyle(
            "candidate", parent=base["Normal"], alignment=TA_LEFT, fontSize=10.5, leading=14,
            leftIndent=14, spaceAfter=4,
        ),
        "gate_pass": ParagraphStyle(
            "gate_pass", parent=base["Normal"], alignment=TA_LEFT, fontSize=9.5, textColor=colors.HexColor("#1a7f37")
        ),
        "gate_fail": ParagraphStyle(
            "gate_fail", parent=base["Normal"], alignment=TA_LEFT, fontSize=9.5, textColor=colors.HexColor("#c0341d")
        ),
        "gate_skip": ParagraphStyle(
            "gate_skip", parent=base["Normal"], alignment=TA_LEFT, fontSize=9.5, textColor=colors.HexColor("#9a6b00")
        ),
        "note": ParagraphStyle(
            "note", parent=base["Normal"], alignment=TA_LEFT, fontSize=9.5, textColor=colors.HexColor("#9a6b00"),
            leftIndent=14, spaceAfter=3,
        ),
    }


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_package_pdf(
    package: dict[str, Any],
    record: JobRecord,
    company_name: str,
    output_path: Path,
) -> Path:
    styles = _styles()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=LETTER,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch,
        title=f"{company_name} -- {record.job_id}",
    )

    story: list[Any] = []
    story.append(Paragraph(_escape(company_name), styles["doc_title"]))
    story.append(
        Paragraph(
            f"Job {record.job_id} &middot; {record.created_at:%Y-%m-%d %H:%M} &middot; status: {record.status}",
            styles["meta"],
        )
    )

    draft = package.get("draft", {})
    body = str(draft.get("body", ""))
    story.append(Paragraph("Draft", styles["h2"]))
    for para in body.split("\n\n"):
        line = para.strip()
        if not line:
            continue
        # Render an email subject line as its own visually distinct line, not a body paragraph.
        if line.lower().startswith("subject:"):
            story.append(Paragraph(f"<b>{_escape(line)}</b>", styles["body"]))
        else:
            story.append(Paragraph(_escape(line).replace("\n", "<br/>"), styles["body"]))

    selected = package.get("microcopy_selected") or {}
    if selected:
        story.append(Paragraph("Selected micro-copy", styles["h2"]))
        for field, text in selected.items():
            story.append(Paragraph(f"<b>{_escape(field_label(field))}:</b> {_escape(str(text))}", styles["body"]))

    microcopy = package.get("microcopy", {})
    if any(microcopy.values()):
        story.append(Paragraph("Micro-copy options", styles["h2"]))
        for field, candidates in microcopy.items():
            if not candidates:
                continue
            story.append(Paragraph(field_label(field), styles["h3"]))
            for cand in candidates:
                text = _escape(str(cand.get("text", "")))
                strategy = cand.get("strategy") or ""
                suffix = f" <i>({_escape(strategy)})</i>" if strategy else ""
                story.append(Paragraph(f"&bull; {text}{suffix}", styles["candidate"]))

    compliance = package.get("compliance_report", {})
    gates = compliance.get("gates", [])
    if gates:
        story.append(Paragraph("Compliance summary", styles["h2"]))
        style_by_status = {"passed": styles["gate_pass"], "failed": styles["gate_fail"], "skipped": styles["gate_skip"]}
        for gate in gates:
            status = str(gate.get("status", "")).lower()
            line = f"{gate.get('gate_name', '?')}: {status.upper()} -- {_escape(str(gate.get('detail', '')))}"
            story.append(Paragraph(line, style_by_status.get(status, styles["body"])))
            for note in gate.get("notes") or []:
                story.append(Paragraph(f"&bull; minor: {_escape(str(note))}", styles["note"]))

    if record.human_touchpoints:
        story.append(Paragraph("Notes for human review", styles["h2"]))
        for note in record.human_touchpoints:
            story.append(Paragraph(f"&bull; {_escape(note)}", styles["note"]))

    story.append(Spacer(1, 0.2 * inch))
    doc.build(story)
    return output_path
