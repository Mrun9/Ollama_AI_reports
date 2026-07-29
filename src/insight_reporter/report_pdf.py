"""Print-ready PDF publishing for immutable generated reports."""

from __future__ import annotations

import html
import io
import json
import re
from collections.abc import Mapping
from pathlib import Path

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from insight_reporter.report_narration import (
    GeneratedReport,
    NarrativeStory,
    included_executive_summary_points,
    included_report_stories,
)

_MAX_PDF_BYTES = 15_000_000
_MAX_CHARTS_PER_STORY = 2
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_SECTION_LABELS = (
    ("key_findings", "Key findings"),
    ("trends_and_changes", "Trends and changes"),
    ("segment_analysis", "Segment analysis"),
    ("anomalies", "Anomalies"),
    ("associations", "Associations"),
    ("benchmarks", "Benchmarks and thresholds"),
    ("manual_visualizations", "Manual visualizations"),
    (
        "data_quality_and_limitations",
        "Data quality and analysis limitations",
    ),
)


class ReportPdfError(ValueError):
    """Raised when a generated report cannot be exported safely."""


def report_pdf_filename(report: GeneratedReport) -> str:
    """Return a bounded download filename derived from the report title."""

    raw_title = str(report.report_settings.get("title", "insight-report"))
    normalized = _SAFE_FILENAME.sub("-", raw_title).strip("-._")
    if not normalized:
        normalized = "insight-report"
    return f"{normalized[:80]}-v{report.version}.pdf"


def build_report_pdf(
    report: GeneratedReport,
    *,
    chart_paths: Mapping[str, Path] | None = None,
) -> bytes:
    """Render the published report with the same verified story content as HTML."""

    stories = included_report_stories(report)
    summary_points = included_executive_summary_points(report)
    if report.items and not stories:
        raise ReportPdfError(
            "Select at least one included story before exporting PDF."
        )
    chart_paths = chart_paths or {}
    styles, font_name, bold_font_name = _styles()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=_plain_text(report.report_settings.get("title", "Report")),
        author=_plain_text(
            report.report_settings.get("report_author", "AI Insight Reporter")
        ),
        subject="Evidence-grounded AI insight report",
    )
    flowables: list[object] = []
    title = _plain_text(report.report_settings.get("title", "Insight report"))
    flowables.extend(
        [
            Paragraph(_markup(title), styles["ReportTitle"]),
            *(
                [
                    Paragraph(
                        _markup(report.report_settings["company_name"]),
                        styles["SummaryHeading"],
                    )
                ]
                if report.report_settings.get("company_name")
                else []
            ),
            Paragraph(
                _markup(
                    f"Version {report.version} | Generated {report.generated_at} "
                    f"| Local model {report.model}"
                    + (
                        " | Narration creativity "
                        + str(
                            report.report_settings[
                                "narration_temperature"
                            ]
                        )
                        if "narration_temperature"
                        in report.report_settings
                        else ""
                    )
                    + (
                        " | Author "
                        + _plain_text(
                            report.report_settings["report_author"]
                        )
                        if report.report_settings.get("report_author")
                        else ""
                    )
                ),
                styles["Metadata"],
            ),
            Spacer(1, 5 * mm),
            Paragraph("Executive summary", styles["Heading1"]),
            Paragraph(
                _markup(
                    "Business objective: "
                    + _plain_text(
                        report.report_settings.get(
                            "business_objective",
                            "Not specified",
                        )
                    )
                ),
                styles["Body"],
            ),
            Paragraph(
                _markup(
                    f"This report presents {len(stories)} published finding"
                    f"{'' if len(stories) == 1 else 's'} across "
                    f"{len(report.kpis)} configured KPI"
                    f"{'' if len(report.kpis) == 1 else 's'}. Numerical claims "
                    "are independently resolved from Python evidence."
                ),
                styles["Body"],
            ),
        ]
    )
    if summary_points:
        flowables.append(
            Paragraph(
                (
                    "AI-generated and Python-validated "
                    f"{len(summary_points)}-point summary"
                    if all(
                        point.narration_source == "ollama"
                        for point in summary_points
                    )
                    else (
                        f"{len(summary_points)}-point summary assembled from "
                        "validated stories"
                    )
                ),
                styles["SummaryHeading"],
            )
        )
        flowables.extend(
            Paragraph(
                _markup(f"• {point.text}"),
                styles["Body"],
            )
            for point in summary_points
        )
    else:
        for story in stories[:3]:
            flowables.extend(
                [
                    Paragraph(_markup(story.headline), styles["SummaryHeading"]),
                    Paragraph(
                        _markup(f"What we found: {story.finding}"),
                        styles["Body"],
                    ),
                    Paragraph(
                        _markup(f"What it may mean: {story.interpretation}"),
                        styles["Body"],
                    ),
                    Paragraph(
                        _markup(
                            f"Confidence - {story.confidence.title()}: "
                            f"{story.confidence_explanation}"
                        ),
                        styles["Caveat"],
                    ),
                ]
            )

    if report.kpis:
        flowables.extend(
            [
                Spacer(1, 3 * mm),
                Paragraph("KPI overview", styles["Heading1"]),
                _kpi_table(
                    report,
                    font_name=font_name,
                    bold_font_name=bold_font_name,
                ),
            ]
        )

    for section, label in _SECTION_LABELS:
        section_stories = tuple(
            story for story in stories if story.section == section
        )
        if not section_stories:
            continue
        flowables.extend(
            [
                Spacer(1, 4 * mm),
                Paragraph(_markup(label), styles["Heading1"]),
            ]
        )
        for story in section_stories:
            flowables.extend(
                _story_flowables(
                    story,
                    styles=styles,
                    chart_paths=chart_paths,
                    font_name=font_name,
                    bold_font_name=bold_font_name,
                )
            )

    if report.generation_limitations:
        flowables.extend(
            [
                Paragraph("Generation limitations", styles["Heading1"]),
                *[
                    Paragraph(
                        _markup(f"- {limitation}"),
                        styles["Body"],
                    )
                    for limitation in report.generation_limitations
                ],
            ]
        )

    flowables.extend(
        [
            Spacer(1, 6 * mm),
            Paragraph("Source traceability", styles["Heading1"]),
        ]
    )
    for source in report.sources:
        source_rows = [
            ["Filename", _plain_text(source.get("internal_filename", ""))],
            ["Format", _plain_text(source.get("format", "")).upper()],
            ["SHA-256", _plain_text(source.get("sha256", ""))],
        ]
        if source.get("table_name"):
            source_rows.insert(
                2,
                ["Worksheet", _plain_text(source.get("table_name", ""))],
            )
        flowables.append(
            _metadata_table(
                source_rows,
                font_name=font_name,
                bold_font_name=bold_font_name,
            )
        )
        flowables.append(Spacer(1, 3 * mm))

    if report.report_settings.get("include_evidence_appendix"):
        flowables.extend(
            [
                PageBreak(),
                Paragraph("Evidence appendix", styles["Heading1"]),
                Paragraph(
                    "The following records are unchanged Python-generated "
                    "observations supporting the published stories.",
                    styles["Body"],
                ),
            ]
        )
        for item in report.items:
            flowables.extend(
                [
                    Paragraph(
                        _markup(f"{item.evidence_id} - {item.title}"),
                        styles["Heading2"],
                    ),
                    _metadata_table(
                        [
                            ["Metric", item.metric],
                            ["Type", item.insight_type],
                            ["Records", str(item.record_count)],
                            [
                                "Evidence confidence",
                                f"{item.confidence.title()} "
                                f"(score {item.confidence_score:g})",
                            ],
                            [
                                "Source columns",
                                ", ".join(item.source_columns) or "None",
                            ],
                            ["Calculation", item.calculation_description],
                        ],
                        font_name=font_name,
                        bold_font_name=bold_font_name,
                    ),
                    Spacer(1, 2 * mm),
                    Preformatted(
                        _plain_text(
                            json.dumps(
                                item.facts,
                                ensure_ascii=False,
                                indent=2,
                                sort_keys=True,
                            )
                        ),
                        styles["Evidence"],
                        maxLineLength=96,
                    ),
                ]
            )
            if item.limitations:
                flowables.extend(
                    Paragraph(
                        _markup(f"- {limitation}"),
                        styles["Small"],
                    )
                    for limitation in item.limitations
                )

    document.build(
        flowables,
        onFirstPage=lambda canvas, doc: _page_frame(
            canvas,
            doc,
            title=title,
            font_name=font_name,
        ),
        onLaterPages=lambda canvas, doc: _page_frame(
            canvas,
            doc,
            title=title,
            font_name=font_name,
        ),
    )
    rendered = buffer.getvalue()
    if not rendered.startswith(b"%PDF-") or len(rendered) > _MAX_PDF_BYTES:
        raise ReportPdfError("Generated PDF output is invalid or too large.")
    return rendered


def _story_flowables(
    story: NarrativeStory,
    *,
    styles: dict[str, ParagraphStyle],
    chart_paths: Mapping[str, Path],
    font_name: str,
    bold_font_name: str,
) -> list[object]:
    flowables: list[object] = [
        HRFlowable(
            width="100%",
            thickness=0.7,
            color=colors.HexColor("#9AB0CF"),
            spaceBefore=2 * mm,
            spaceAfter=3 * mm,
        ),
        Paragraph(_markup(story.headline), styles["Heading2"]),
        Paragraph(
            _markup(f"What we found: {story.finding}"),
            styles["Finding"],
        ),
        Paragraph(
            _markup(f"What it may mean: {story.interpretation}"),
            styles["Body"],
        ),
        Paragraph(
            _markup(f"Suggested next steps: {story.follow_up}"),
            styles["Body"],
        ),
        Paragraph(
            _markup(f"Keep in mind: {story.caveat}"),
            styles["Caveat"],
        ),
        Paragraph(
            _markup(
                f"Confidence - {story.confidence.title()}: "
                f"{story.confidence_explanation}"
            ),
            styles["Caveat"],
        ),
    ]
    if story.fact_references:
        claim_rows = [["Verified claim", "Value", "Evidence"]]
        claim_rows.extend(
            [
                [
                    fact.label,
                    fact.formatted_value,
                    fact.evidence_id,
                ]
                for fact in story.fact_references
            ]
        )
        flowables.append(
            Table(
                [
                    [
                        Paragraph(_markup(cell), styles["TableCell"])
                        for cell in row
                    ]
                    for row in claim_rows
                ],
                colWidths=[88 * mm, 32 * mm, 48 * mm],
                repeatRows=1,
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
                        ("FONTNAME", (0, 0), (-1, 0), bold_font_name),
                        ("FONTNAME", (0, 1), (-1, -1), font_name),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#AAB2BD")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                ),
            )
        )
    included_chart_paths = [
        (evidence_id, chart_paths[evidence_id])
        for evidence_id in story.evidence_ids
        if evidence_id in chart_paths
        and chart_paths[evidence_id].is_file()
    ][:_MAX_CHARTS_PER_STORY]
    for evidence_id, chart_path in included_chart_paths:
        flowables.append(
            KeepTogether(
                [
                Spacer(1, 3 * mm),
                Paragraph(
                    _markup(f"Supporting chart - {evidence_id}"),
                    styles["Small"],
                ),
                _chart_image(chart_path),
                ]
            )
        )
    flowables.append(
        Paragraph(
            _markup(
                "Supporting evidence: " + ", ".join(story.evidence_ids)
            ),
            styles["Small"],
        )
    )
    return flowables


def _chart_image(path: Path) -> Image:
    try:
        width, height = ImageReader(str(path)).getSize()
    except Exception as error:
        raise ReportPdfError("A supporting chart could not be read.") from error
    maximum_width = 168 * mm
    maximum_height = 92 * mm
    scale = min(maximum_width / width, maximum_height / height, 1.0)
    return Image(
        str(path),
        width=width * scale,
        height=height * scale,
        hAlign="CENTER",
    )


def _kpi_table(
    report: GeneratedReport,
    *,
    font_name: str,
    bold_font_name: str,
) -> Table:
    styles = getSampleStyleSheet()
    rows = [["KPI", "Type", "Direction"]]
    rows.extend(
        [
            [
                _plain_text(kpi.get("name", "")),
                _plain_text(kpi.get("metric_type", "")),
                _plain_text(kpi.get("kpi_direction", "not specified")),
            ]
            for kpi in report.kpis
        ]
    )
    return Table(
        [
            [
                Paragraph(
                    _markup(cell),
                    ParagraphStyle(
                        "KpiCell",
                        parent=styles["BodyText"],
                        fontName=font_name,
                        fontSize=8.5,
                        leading=11,
                    ),
                )
                for cell in row
            ]
            for row in rows
        ],
        colWidths=[82 * mm, 42 * mm, 44 * mm],
        repeatRows=1,
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE7F5")),
                ("FONTNAME", (0, 0), (-1, 0), bold_font_name),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#AAB2BD")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        ),
    )


def _metadata_table(
    rows: list[list[str]],
    *,
    font_name: str,
    bold_font_name: str,
) -> Table:
    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle(
        "MetadataCell",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=8,
        leading=10,
    )
    table = Table(
        [
            [
                Paragraph(_markup(_plain_text(row[0])), cell_style),
                Paragraph(_markup(_plain_text(row[1])), cell_style),
            ]
            for row in rows
        ],
        colWidths=[38 * mm, 130 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), bold_font_name),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C4CAD2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _styles() -> tuple[dict[str, ParagraphStyle], str, str]:
    font_name, bold_font_name = _register_fonts()
    sample = getSampleStyleSheet()
    styles = {
        "ReportTitle": ParagraphStyle(
            "ReportTitle",
            parent=sample["Title"],
            fontName=bold_font_name,
            fontSize=22,
            leading=27,
            textColor=colors.HexColor("#17365D"),
            alignment=TA_LEFT,
            spaceAfter=6,
        ),
        "Metadata": ParagraphStyle(
            "Metadata",
            parent=sample["BodyText"],
            fontName=font_name,
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#5C6570"),
        ),
        "Heading1": ParagraphStyle(
            "Heading1",
            parent=sample["Heading1"],
            fontName=bold_font_name,
            fontSize=15,
            leading=19,
            textColor=colors.HexColor("#17365D"),
            spaceBefore=8,
            spaceAfter=6,
        ),
        "Heading2": ParagraphStyle(
            "Heading2",
            parent=sample["Heading2"],
            fontName=bold_font_name,
            fontSize=11.5,
            leading=14,
            textColor=colors.HexColor("#244A77"),
            spaceBefore=4,
            spaceAfter=4,
        ),
        "SummaryHeading": ParagraphStyle(
            "SummaryHeading",
            parent=sample["Heading3"],
            fontName=bold_font_name,
            fontSize=10.5,
            leading=13,
            textColor=colors.HexColor("#244A77"),
            spaceBefore=5,
            spaceAfter=2,
        ),
        "Body": ParagraphStyle(
            "Body",
            parent=sample["BodyText"],
            fontName=font_name,
            fontSize=9.2,
            leading=13,
            spaceAfter=4,
        ),
        "Finding": ParagraphStyle(
            "Finding",
            parent=sample["BodyText"],
            fontName=bold_font_name,
            fontSize=9.5,
            leading=13,
            textColor=colors.HexColor("#1E2F43"),
            spaceAfter=4,
        ),
        "Caveat": ParagraphStyle(
            "Caveat",
            parent=sample["BodyText"],
            fontName=font_name,
            fontSize=8.6,
            leading=11,
            textColor=colors.HexColor("#6A4D14"),
            backColor=colors.HexColor("#FFF7E3"),
            borderPadding=5,
            spaceAfter=5,
        ),
        "Small": ParagraphStyle(
            "Small",
            parent=sample["BodyText"],
            fontName=font_name,
            fontSize=7.5,
            leading=9.5,
            textColor=colors.HexColor("#5C6570"),
            spaceAfter=3,
        ),
        "Evidence": ParagraphStyle(
            "Evidence",
            parent=sample["Code"],
            fontName="Courier",
            fontSize=6.7,
            leading=8.2,
            backColor=colors.HexColor("#F5F6F8"),
            borderPadding=5,
            spaceAfter=5,
        ),
        "TableCell": ParagraphStyle(
            "TableCell",
            parent=sample["BodyText"],
            fontName=font_name,
            fontSize=7.8,
            leading=9.5,
        ),
    }
    return styles, font_name, bold_font_name


def _register_fonts() -> tuple[str, str]:
    fonts_dir = Path(reportlab.__file__).resolve().parent / "fonts"
    regular = fonts_dir / "Vera.ttf"
    bold = fonts_dir / "VeraBd.ttf"
    if regular.is_file() and bold.is_file():
        try:
            if "InsightVera" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("InsightVera", regular))
            if "InsightVeraBold" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("InsightVeraBold", bold))
            return "InsightVera", "InsightVeraBold"
        except (OSError, TTFError):
            pass
    return "Helvetica", "Helvetica-Bold"


def _page_frame(
    canvas,  # type: ignore[no-untyped-def]
    document,  # type: ignore[no-untyped-def]
    *,
    title: str,
    font_name: str,
) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D4D9E0"))
    canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
    canvas.setFont(font_name, 7)
    canvas.setFillColor(colors.HexColor("#68727E"))
    canvas.drawString(18 * mm, 9 * mm, _plain_text(title)[:80])
    canvas.drawRightString(
        A4[0] - 18 * mm,
        9 * mm,
        f"Page {document.page}",
    )
    canvas.restoreState()


def _markup(value: object) -> str:
    return html.escape(_plain_text(value), quote=True).replace("\n", "<br/>")


def _plain_text(value: object) -> str:
    text = value if isinstance(value, str) else str(value)
    return (
        "".join(character for character in text if character >= " " or character == "\n")
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
    )
