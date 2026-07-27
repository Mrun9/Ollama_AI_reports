"""Milestone 5C PDF publishing tests."""

import io
import json
from pathlib import Path

from PIL import Image as PillowImage
from pypdf import PdfReader

from insight_reporter.report_generation_package import (
    ReportGenerationPackage,
)
from insight_reporter.report_narration import (
    generate_narrated_report,
    publish_report_presentation,
)
from insight_reporter.report_pdf import (
    build_report_pdf,
    report_pdf_filename,
)


class _StoryClient:
    def __init__(self) -> None:
        self.call_count = 0

    def chat(self, **kwargs: object) -> object:
        properties = kwargs["format"]["properties"]
        references = properties["fact_references"]["items"]["enum"]
        headline = (
            "Primary revenue pattern merits review"
            if self.call_count == 0
            else "Secondary revenue pattern merits review"
        )
        self.call_count += 1
        return {
            "message": {
                "content": json.dumps(
                    {
                        "story_id": properties["story_id"]["enum"][0],
                        "headline": headline,
                        "finding": (
                            "The verified revenue result is 123.45."
                        ),
                        "interpretation": (
                            "The combined evidence is relevant to the "
                            "configured objective."
                        ),
                        "follow_up": (
                            "Monitor whether the pattern persists in future "
                            "validated data."
                        ),
                        "caveat": (
                            "The evidence is descriptive and does not "
                            "establish causation."
                        ),
                        "fact_references": references[:1],
                    }
                )
            }
        }


def _report(*, record_count: int = 1):
    package = ReportGenerationPackage(
        schema_version=1,
        dataset_id="a" * 32,
        report_configuration_sha256="b" * 64,
        report_settings={
            "title": "Revenue / Regional Report",
            "business_objective": "Review validated revenue performance.",
            "audience": "management",
            "tone": "professional",
            "detail_level": "standard",
            "user_notes": {
                "content": "",
                "source": "user_provided",
            },
            "include_evidence_appendix": True,
        },
        sources=(
            {
                "source_id": "SRC-ONE",
                "internal_filename": "sales.csv",
                "format": "csv",
                "sha256": "c" * 64,
                "table_name": None,
            },
        ),
        kpis=(
            {
                "metric_id": "MET-ABCDEF123456",
                "name": "revenue",
                "metric_type": "source",
                "kpi_direction": "higher",
            },
        ),
        deterministic_evidence=tuple(
            {
                "id": f"EVD-{index:016X}",
                "insight_id": f"INS-{index:03d}",
                "insight_type": "segment_ranking",
                "metric_id": "MET-ABCDEF123456",
                "metric": "revenue",
                "kpi_definition": {},
                "source_columns": ["revenue", "segment"],
                "filters": {},
                "periods": [],
                "calculation_description": (
                    "Python compared validated revenue values by segment."
                ),
                "observation": {
                    "highest_segment": "North",
                    "value": 123.45,
                },
                "record_count": 20,
                "ranking": {"rank": 1},
                "limitations": [],
                "chart": (
                    {
                        "filename": "d" * 32 + ".png",
                        "title": "Revenue by segment",
                        "alt_text": (
                            "Bar chart comparing revenue by segment"
                        ),
                    }
                    if index == 1
                    else None
                ),
                "supporting_data": [],
                "supporting_data_omitted_count": 0,
            }
            for index in range(1, record_count + 1)
        ),
        manual_visualization_evidence=(),
        omissions={},
        model_input_policy={"raw_dataset_rows_included": False},
    )
    return generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=_StoryClient(),
    )


def test_pdf_contains_published_stories_evidence_and_chart(
    tmp_path: Path,
) -> None:
    report = _report()
    chart_path = tmp_path / ("d" * 32 + ".png")
    PillowImage.new("RGB", (640, 360), color=(225, 235, 248)).save(
        chart_path,
        format="PNG",
    )

    rendered = build_report_pdf(
        report,
        chart_paths={"EVD-0000000000000001": chart_path},
    )
    reader = PdfReader(io.BytesIO(rendered))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert rendered.startswith(b"%PDF-")
    assert len(reader.pages) >= 2
    assert "Executive summary" in extracted
    assert "Primary revenue pattern merits review" in extracted
    assert "123.45" in extracted
    assert "Source traceability" in extracted
    assert "Evidence appendix" in extracted
    assert any(page.images for page in reader.pages)


def test_pdf_filename_is_safe_and_versioned() -> None:
    report = _report()

    filename = report_pdf_filename(report)

    assert filename == "Revenue-Regional-Report-v0.pdf"
    assert "/" not in filename


def test_excluded_stories_do_not_appear_in_pdf() -> None:
    report = _report(record_count=4)
    first_story, second_story = report.stories
    revised = publish_report_presentation(
        report,
        included_story_ids=(second_story.story_id,),
        story_order=(second_story.story_id, first_story.story_id),
    )

    rendered = build_report_pdf(revised)
    extracted = "\n".join(
        page.extract_text() or ""
        for page in PdfReader(io.BytesIO(rendered)).pages
    )

    assert second_story.headline in extracted
    assert first_story.headline not in extracted
