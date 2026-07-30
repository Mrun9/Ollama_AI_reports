"""Milestone 5B.1 synthesis trust-boundary and persistence tests."""

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from insight_reporter.manual_visualization_evidence import (
    ManualVisualizationEvidence,
)
from insight_reporter.model_run_metrics import model_metrics_csv_path
from insight_reporter.report_generation_package import (
    ReportGenerationPackage,
)
from insight_reporter.report_narration import (
    ReportNarrationError,
    generate_narrated_report,
    generated_report_chart_snapshots,
    included_report_stories,
    latest_generated_report,
    list_generated_report_versions,
    load_generated_report,
    load_generated_report_version,
    publish_report_presentation,
    regenerate_generated_story,
    save_generated_report,
    snapshot_generated_report_charts,
)


class FakeClient:
    def __init__(
        self,
        *,
        commentary: str = (
            "This evidence highlights a meaningful comparison while keeping "
            "the interpretation descriptive."
        ),
    ) -> None:
        self.commentary = commentary
        self.calls: list[dict[str, object]] = []

    def chat(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        schema = kwargs["format"]
        properties = schema["properties"]
        if "points" in properties:
            prompt = kwargs["messages"][1]["content"]
            report_payload = json.loads(prompt.split("\n", maxsplit=1)[1])
            stories = report_payload["stories"]
            qualifiers = (
                "Primary",
                "Secondary",
                "Additional",
                "Related",
                "Supporting",
            )
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "points": [
                                _management_point(
                                    story,
                                    qualifier=qualifiers[index],
                                )
                                for index, story in enumerate(
                                    stories[index % len(stories)]
                                    for index in range(5)
                                )
                            ]
                        }
                    )
                }
            }
        story_id = properties["story_id"]["enum"][0]
        fact_references = properties["fact_references"]["items"].get(
            "enum",
            [],
        )
        return {
            "message": {
                "content": json.dumps(
                    {
                        "story_id": story_id,
                        "headline": "A verified pattern merits review",
                        "finding": self.commentary,
                        "interpretation": (
                            "The combined evidence is relevant to the "
                            "configured business objective."
                        ),
                        "follow_up": (
                            "Review the pattern and monitor whether it "
                            "persists in future validated data."
                        ),
                        "caveat": (
                            "The evidence is descriptive and does not "
                            "establish causation."
                        ),
                        "fact_references": fact_references[:2],
                    }
                )
            }
        }


def _management_point(
    story: dict[str, object],
    *,
    qualifier: str,
) -> dict[str, object]:
    facts = story["available_fact_references"]
    fact = facts[0]
    business_context = story.get("verified_business_context", [])
    context = (
        business_context[0]["value"]
        if business_context
        else "the verified scope"
    )
    return {
        "finding": (
            f"{qualifier} {story['metric']} result is "
            f"{fact['display_value']} for {context}."
        ),
        "business_implication": (
            f"This {story['metric']} result is relevant to the "
            "configured business objective."
        ),
        "recommended_action": (
            f"Review the {context} result and monitor the next validated period."
        ),
        "story_ids": [story["story_id"]],
        "fact_references": [fact["reference"]],
    }


def _package(*, record_count: int = 5) -> ReportGenerationPackage:
    records = tuple(
        {
            "id": f"EVD-{index:016X}",
            "insight_id": f"INS-{index:03d}",
            "insight_type": (
                "numeric_correlation" if index == 1 else "segment_ranking"
            ),
            "metric_id": "MET-ABCDEF123456",
            "metric": "revenue",
            "kpi_definition": {},
            "source_columns": ["revenue", "segment"],
            "filters": {},
            "periods": [],
            "calculation_description": (
                "Python compared validated source values."
            ),
            "observation": {
                "highest_segment": "North",
                "value": 123.45,
            },
            "record_count": 20,
            "ranking": {"rank": index},
            "limitations": (
                ["Association does not establish causation."]
                if index == 1
                else []
            ),
            "chart": None,
            "supporting_data": [],
            "supporting_data_omitted_count": 0,
        }
        for index in range(1, record_count + 1)
    )
    return ReportGenerationPackage(
        schema_version=1,
        dataset_id="a" * 32,
        report_configuration_sha256="b" * 64,
        report_settings={
            "title": "Trusted report",
            "business_objective": "Review validated performance.",
            "audience": "management",
            "tone": "professional",
            "detail_level": "standard",
            "user_notes": {
                "content": "User context",
                "source": "user_provided",
            },
            "include_evidence_appendix": True,
        },
        sources=(
            {
                "source_id": "SRC-ONE",
                "internal_filename": "a.csv",
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
            },
        ),
        deterministic_evidence=records,
        manual_visualization_evidence=(),
        omissions={},
        model_input_policy={"raw_dataset_rows_included": False},
    )


def test_narration_synthesizes_story_packs_and_is_traceable(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    metrics_dir = tmp_path / "metrics"

    report = generate_narrated_report(
        _package(),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=client,
        metrics_dir=metrics_dir,
    )

    assert len(client.calls) == 3
    assert len(report.items) == 5
    assert len(report.stories) == 2
    assert report.ai_narrated_evidence_ids == tuple(
        item.evidence_id for item in report.items
    )
    assert report.deterministic_only_evidence_ids == ()
    assert all(
        story.narration_source == "ollama"
        for story in report.stories
    )
    assert report.schema_version == 8
    assert len(report.executive_summary) == 5
    assert all(
        point.narration_source == "ollama"
        for point in report.executive_summary
    )
    assert report.items[0].facts == {
        "highest_segment": "North",
        "value": 123.45,
    }
    assert report.stories[0].business_context[0] == {
        "metric": "revenue",
        "path": "facts.highest_segment",
        "value": "North",
    }
    assert report.stories[0].fact_references[0].value == 123.45
    assert (
        report.stories[0].fact_references[0].formatted_value
        == "123.45"
    )
    assert len(report.stories[0].evidence_ids) == 3
    assert len(
        {
            fact.evidence_id
            for fact in report.stories[0].fact_references
        }
    ) == 2
    first_prompt = client.calls[0]["messages"][1]["content"]
    assert '"display_value":"123.45"' in first_prompt
    assert '"value":null' not in first_prompt
    assert "supporting_data" not in first_prompt
    assert "available_fact_references" in first_prompt
    assert "verified_business_context" in first_prompt
    assert client.calls[0]["think"] is False
    assert client.calls[0]["options"]["num_ctx"] == 4_096
    assert client.calls[0]["options"]["temperature"] == 0.35
    assert report.report_settings["narration_temperature"] == 0.35
    with model_metrics_csv_path(metrics_dir).open(
        encoding="utf-8",
        newline="",
    ) as handle:
        metric_rows = list(csv.DictReader(handle))
    assert [row["task_type"] for row in metric_rows] == [
        "report_story",
        "report_story",
        "executive_summary",
    ]
    assert len({row["workflow_run_id"] for row in metric_rows}) == 1
    assert all(row["status"] == "validated" for row in metric_rows)
    assert report.generation_diagnostics == {
        "schema_version": 1,
        "story_pack_count": 2,
        "ai_story_count": 2,
        "fallback_story_count": 0,
        "rejected_story_ids": [],
        "executive_summary_source": "ollama",
        "validation_policy": {
            "exact_numeric_grounding": True,
            "verified_context_only": True,
            "causal_claims_allowed": False,
            "management_summary_fields": [
                "finding",
                "business_implication",
                "recommended_action",
            ],
        },
    }
    assert report.stories[0].confidence == "medium"
    assert "not a prediction probability" in (
        report.stories[0].confidence_explanation
    )


def test_executive_summary_accepts_only_selected_exact_values() -> None:
    class SummaryClient(FakeClient):
        def chat(self, **kwargs: object) -> object:
            if "points" not in kwargs["format"]["properties"]:
                return super().chat(**kwargs)
            self.calls.append(kwargs)
            prompt = kwargs["messages"][1]["content"]
            report_payload = json.loads(prompt.split("\n", maxsplit=1)[1])
            story = report_payload["stories"][0]
            reference = story["available_fact_references"][0]["reference"]
            display_value = story["available_fact_references"][0][
                "display_value"
            ]
            qualifiers = (
                "Primary",
                "Secondary",
                "Additional",
                "Related",
                "Supporting",
            )
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "points": [
                                {
                                    "finding": (
                                        "Primary revenue finding is 123.45 "
                                        "for North and merits review."
                                        if index == 0
                                        else (
                                            f"{qualifier} revenue finding "
                                            f"is {display_value} for North."
                                        )
                                    ),
                                    "business_implication": (
                                        "This revenue result is relevant to "
                                        "the configured objective."
                                    ),
                                    "recommended_action": (
                                        "Review the North result and monitor "
                                        "the next validated period."
                                    ),
                                    "story_ids": [story["story_id"]],
                                    "fact_references": [reference],
                                }
                                for index, qualifier in enumerate(qualifiers)
                            ]
                        }
                    )
                }
            }

    report = generate_narrated_report(
        _package(record_count=1),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=SummaryClient(),
    )

    assert report.executive_summary[0].text == (
        "Primary revenue finding is 123.45 for North and merits review."
    )
    assert report.executive_summary[0].business_implication
    assert report.executive_summary[0].recommended_action.startswith("Review")
    assert report.executive_summary[0].fact_references[0].value == 123.45
    assert all(
        point.narration_source == "ollama"
        for point in report.executive_summary
    )


def test_executive_summary_retries_when_business_context_is_ignored() -> None:
    class ContextRetryClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.summary_calls = 0

        def chat(self, **kwargs: object) -> object:
            if "points" not in kwargs["format"]["properties"]:
                return super().chat(**kwargs)
            self.summary_calls += 1
            if self.summary_calls > 1:
                return super().chat(**kwargs)
            self.calls.append(kwargs)
            prompt = kwargs["messages"][1]["content"]
            report_payload = json.loads(prompt.split("\n", maxsplit=1)[1])
            story = report_payload["stories"][0]
            fact = story["available_fact_references"][0]
            qualifiers = (
                "Primary",
                "Secondary",
                "Additional",
                "Related",
                "Supporting",
            )
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "points": [
                                {
                                    "finding": (
                                        f"{qualifier} revenue result is "
                                        f"{fact['display_value']}."
                                    ),
                                    "business_implication": (
                                        "The revenue result is relevant to "
                                        "the configured objective."
                                    ),
                                    "recommended_action": (
                                        "Review the result and monitor the "
                                        "next validated period."
                                    ),
                                    "story_ids": [story["story_id"]],
                                    "fact_references": [fact["reference"]],
                                }
                                for qualifier in qualifiers
                            ]
                        }
                    )
                }
            }

    client = ContextRetryClient()
    report = generate_narrated_report(
        _package(record_count=1),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=client,
    )

    assert client.summary_calls == 2
    assert all(
        "North" in point.text for point in report.executive_summary
    )


def test_product_names_are_retained_as_verified_business_context() -> None:
    package = _package(record_count=1)
    record = dict(package.deterministic_evidence[0])
    record["observation"] = {
        "category_column": "product",
        "top_segment": {
            "segment": "Widget Pro",
            "value": 175,
            "record_count": 8,
        },
        "bottom_segment": {
            "segment": "Widget Basic",
            "value": 80,
            "record_count": 6,
        },
    }
    package = replace(package, deterministic_evidence=(record,))

    report = generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(),
    )

    assert [
        context["value"] for context in report.stories[0].business_context
    ] == ["Widget Pro", "Widget Basic"]
    assert all(
        "Widget Pro" in point.text for point in report.executive_summary
    )


def test_cohort_comparison_prioritizes_named_management_change() -> None:
    package = _package(record_count=1)
    record = dict(package.deterministic_evidence[0])
    record["insight_type"] = "cohort_period_comparison"
    record["observation"] = {
        "category_column": "region",
        "previous_period": "2026-Q1",
        "current_period": "2026-Q2",
        "best_performing_change": {
            "cohort": "North",
            "previous_value": 100,
            "current_value": 140,
            "absolute_change": 40,
            "percentage_change": 40,
        },
        "worst_performing_change": {
            "cohort": "South",
            "previous_value": 100,
            "current_value": 70,
            "absolute_change": -30,
            "percentage_change": -30,
        },
    }
    package = replace(package, deterministic_evidence=(record,))

    report = generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(),
    )

    story = report.stories[0]
    assert story.section == "segment_analysis"
    assert [
        context["value"] for context in story.business_context
    ] == ["2026-Q1", "2026-Q2", "North", "South"]
    assert story.fact_references[0].path == (
        "facts.worst_performing_change.percentage_change"
    )
    assert story.fact_references[0].value == -30


def test_executive_summary_accepts_verified_quarter_and_region_context() -> None:
    package = _package(record_count=1)
    record = dict(package.deterministic_evidence[0])
    record["insight_type"] = "period_change"
    record["observation"] = {
        "previous_period": "2026-Q1",
        "current_period": "2026-Q2",
        "current_value": 125,
        "percentage_change": 25,
        "direction": "increasing",
        "region": "North",
    }
    package = replace(package, deterministic_evidence=(record,))

    class ContextSummaryClient(FakeClient):
        def chat(self, **kwargs: object) -> object:
            if "points" not in kwargs["format"]["properties"]:
                return super().chat(**kwargs)
            self.calls.append(kwargs)
            prompt = kwargs["messages"][1]["content"]
            report_payload = json.loads(prompt.split("\n", maxsplit=1)[1])
            story = report_payload["stories"][0]
            percentage_fact = next(
                fact
                for fact in story["available_fact_references"]
                if "percentage change" in fact["label"]
            )
            qualifiers = (
                "Primary",
                "Secondary",
                "Additional",
                "Related",
                "Supporting",
            )
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "points": [
                                {
                                    "finding": (
                                        f"{qualifier} revenue increased by "
                                        f"{percentage_fact['display_value']}% "
                                        "in 2026-Q2 for North."
                                    ),
                                    "business_implication": (
                                        "The revenue movement is material to "
                                        "the configured objective."
                                    ),
                                    "recommended_action": (
                                        "Compare North with the next validated "
                                        "quarter and monitor whether it persists."
                                    ),
                                    "story_ids": [story["story_id"]],
                                    "fact_references": [
                                        percentage_fact["reference"]
                                    ],
                                }
                                for qualifier in qualifiers
                            ]
                        }
                    )
                }
            }

    report = generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=ContextSummaryClient(),
    )

    assert [
        context["value"] for context in report.stories[0].business_context
    ] == ["2026-Q1", "2026-Q2", "North"]
    assert "25% in 2026-Q2 for North" in report.executive_summary[0].text


def test_invalid_executive_summary_falls_back_to_validated_stories() -> None:
    class InvalidSummaryClient(FakeClient):
        def chat(self, **kwargs: object) -> object:
            if "points" not in kwargs["format"]["properties"]:
                return super().chat(**kwargs)
            self.calls.append(kwargs)
            prompt = kwargs["messages"][1]["content"]
            report_payload = json.loads(prompt.split("\n", maxsplit=1)[1])
            story = report_payload["stories"][0]
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "points": [
                                {
                                    "finding": (
                                        f"Revenue invented result {999 + index}."
                                    ),
                                    "business_implication": (
                                        "This revenue result merits review."
                                    ),
                                    "recommended_action": (
                                        "Review the result and monitor the "
                                        "next period."
                                    ),
                                    "story_ids": [story["story_id"]],
                                    "fact_references": [],
                                }
                                for index in range(5)
                            ]
                        }
                    )
                }
            }

    report = generate_narrated_report(
        _package(record_count=1),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=InvalidSummaryClient(),
    )

    assert len(report.executive_summary) == 5
    assert all(
        point.narration_source == "deterministic_only"
        for point in report.executive_summary
    )
    assert any(
        "five-point executive summary" in limitation
        for limitation in report.generation_limitations
    )


def test_deterministic_summary_caps_facts_and_loads_older_oversized_fallback(
    tmp_path: Path,
) -> None:
    package = _package(record_count=1)
    record = dict(package.deterministic_evidence[0])
    record["observation"] = {
        "highest_segment": "North",
        "current_value": 150,
        "previous_value": 120,
        "absolute_change": 30,
        "percentage_change": 25,
        "target": 140,
    }
    package = replace(package, deterministic_evidence=(record,))

    class ManyFactsFallbackClient(FakeClient):
        def chat(self, **kwargs: object) -> object:
            properties = kwargs["format"]["properties"]
            if "points" in properties:
                self.calls.append(kwargs)
                prompt = kwargs["messages"][1]["content"]
                report_payload = json.loads(
                    prompt.split("\n", maxsplit=1)[1]
                )
                story = report_payload["stories"][0]
                return {
                    "message": {
                        "content": json.dumps(
                            {
                                "points": [
                                    {
                                        "finding": (
                                            "Revenue invented result "
                                            f"{900 + index}."
                                        ),
                                        "business_implication": (
                                            "The revenue result merits review."
                                        ),
                                        "recommended_action": (
                                            "Review the result and monitor "
                                            "the next period."
                                        ),
                                        "story_ids": [story["story_id"]],
                                        "fact_references": [],
                                    }
                                    for index in range(5)
                                ]
                            }
                        )
                    }
                }
            self.calls.append(kwargs)
            references = properties["fact_references"]["items"]["enum"]
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "story_id": properties["story_id"]["enum"][0],
                            "headline": "North revenue merits review",
                            "finding": (
                                "Revenue performance for North is ready for "
                                "descriptive review."
                            ),
                            "interpretation": (
                                "The result is relevant to the objective."
                            ),
                            "follow_up": (
                                "Review North and monitor the next period."
                            ),
                            "caveat": "The analysis remains descriptive.",
                            "fact_references": references,
                        }
                    )
                }
            }

    report = generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=ManyFactsFallbackClient(),
    )

    assert len(report.stories[0].fact_references) == 5
    assert max(
        len(point.fact_references)
        for point in report.executive_summary
    ) == 3
    saved, path = save_generated_report(
        report,
        generated_report_dir=tmp_path,
    )
    assert load_generated_report(
        saved.dataset_id,
        saved.report_id,
        generated_report_dir=tmp_path,
    ).version == 1

    legacy_payload = json.loads(path.read_text(encoding="utf-8"))
    legacy_payload["executive_summary"][0]["fact_references"].extend(
        legacy_payload["stories"][0]["fact_references"][3:5]
    )
    path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    compatible = load_generated_report(
        saved.dataset_id,
        saved.report_id,
        generated_report_dir=tmp_path,
    )
    assert len(compatible.executive_summary[0].fact_references) == 5


def test_story_ids_are_stable_for_the_same_evidence_pack() -> None:
    package = _package(record_count=4)

    first = generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(),
    )
    second = generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(),
    )

    assert tuple(story.story_id for story in first.stories) == tuple(
        story.story_id for story in second.stories
    )
    assert first.report_id != second.report_id


def test_lower_priority_evidence_remains_in_the_appendix() -> None:
    report = generate_narrated_report(
        _package(record_count=12),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(),
    )

    synthesized_ids = {
        evidence_id
        for story in report.stories
        for evidence_id in story.evidence_ids
    }
    assert len(synthesized_ids) == 10
    assert len(report.items) == 12
    assert set(report.deterministic_only_evidence_ids) == {
        "EVD-000000000000000B",
        "EVD-000000000000000C",
    }


def test_story_presentation_creates_a_reordered_immutable_revision() -> None:
    report = generate_narrated_report(
        _package(record_count=4),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(),
    )
    first_story, second_story = report.stories

    revised = publish_report_presentation(
        report,
        included_story_ids=(second_story.story_id,),
        story_order=(second_story.story_id, first_story.story_id),
    )

    assert revised.report_id == report.report_id
    assert revised.version == 0
    assert revised.stories[0].story_id == second_story.story_id
    assert revised.stories[0].display_order == 1
    assert revised.stories[0].included is True
    assert revised.stories[1].included is False
    assert included_report_stories(revised) == (revised.stories[0],)


def test_one_story_can_be_regenerated_without_changing_others() -> None:
    package = _package(record_count=4)
    report = generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(),
    )
    target = report.stories[0]
    untouched = report.stories[1]

    revised = regenerate_generated_story(
        report,
        package,
        story_id=target.story_id,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(
            commentary="The regenerated evidence supports focused review."
        ),
    )

    assert revised.report_id == report.report_id
    assert revised.version == 0
    assert revised.stories[0].finding == (
        "The regenerated evidence supports focused review."
    )
    assert revised.stories[1] == untouched


def test_deterministic_chart_metadata_is_retained_for_publishing() -> None:
    package = _package(record_count=1)
    record = dict(package.deterministic_evidence[0])
    record["chart"] = {
        "filename": "a" * 32 + ".png",
        "title": "Revenue by segment",
        "alt_text": "Bar chart comparing revenue by segment",
    }
    package = replace(package, deterministic_evidence=(record,))

    report = generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(),
    )

    assert report.items[0].chart_filename == "a" * 32 + ".png"
    assert report.items[0].chart_title == "Revenue by segment"
    assert report.items[0].chart_alt_text.startswith("Bar chart")


def test_exact_referenced_number_is_allowed_in_commentary() -> None:
    report = generate_narrated_report(
        _package(record_count=1),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(
            commentary=(
                "The verified result of 123.45 supports a descriptive "
                "comparison."
            )
        ),
    )

    assert report.stories[0].narration_source == "ollama"
    assert "123.45" in report.stories[0].finding
    assert (
        report.stories[0].fact_references[0].formatted_value
        == "123.45"
    )


def test_multiple_python_verified_numbers_are_allowed_naturally() -> None:
    package = _package(record_count=1)
    record = dict(package.deterministic_evidence[0])
    record["observation"] = {
        "percentage_change": 23.45,
        "current_value": 123.45,
        "previous_value": 100,
    }
    record["ranking"] = {"confidence": 1.0, "rank": 1}
    package = replace(package, deterministic_evidence=(record,))
    client = FakeClient(
        commentary=(
            "Revenue reached 123.45 after a verified percentage change of "
            "23.45%, making this one area to review."
        )
    )

    report = generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        temperature=0.6,
        client=client,
    )

    assert report.stories[0].narration_source == "ollama"
    assert "123.45" in report.stories[0].finding
    assert "23.45%" in report.stories[0].finding
    assert "one area" in report.stories[0].finding
    assert report.stories[0].confidence == "high"
    assert client.calls[0]["options"]["temperature"] == 0.6
    assert len(report.stories[0].fact_references) == 2


def test_invalid_story_is_retried_with_validation_feedback() -> None:
    class RetryClient(FakeClient):
        def chat(self, **kwargs: object) -> object:
            if "points" in kwargs["format"]["properties"]:
                return super().chat(**kwargs)
            self.calls.append(kwargs)
            schema = kwargs["format"]
            properties = schema["properties"]
            story_id = properties["story_id"]["enum"][0]
            references = properties["fact_references"]["items"]["enum"]
            finding = (
                "Review these results in 2 steps."
                if len(self.calls) == 1
                else "This evidence supports a descriptive comparison."
            )
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "story_id": story_id,
                            "headline": "A verified pattern merits review",
                            "finding": finding,
                            "interpretation": (
                                "The evidence is relevant to the objective."
                            ),
                            "follow_up": "Review whether the pattern persists.",
                            "caveat": "The analysis remains descriptive.",
                            "fact_references": references[:1],
                        }
                    )
                }
            }

    client = RetryClient()
    report = generate_narrated_report(
        _package(record_count=1),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        temperature=0.35,
        client=client,
    )

    assert report.stories[0].narration_source == "ollama"
    story_calls = [
        call
        for call in client.calls
        if "story_id" in call["format"]["properties"]
    ]
    assert len(story_calls) == 2
    assert story_calls[1]["options"]["temperature"] == 0.1
    assert "Python rejected that response" in (
        story_calls[1]["messages"][-1]["content"]
    )


@pytest.mark.parametrize("temperature", [-0.1, 1.1, float("nan"), True])
def test_invalid_report_temperature_is_rejected(
    temperature: object,
) -> None:
    with pytest.raises(ReportNarrationError, match="temperature"):
        generate_narrated_report(
            _package(record_count=1),
            model="llama3.2:latest",
            host="http://127.0.0.1:11434",
            timeout_seconds=120,
            temperature=temperature,  # type: ignore[arg-type]
            client=FakeClient(),
        )


def test_percentage_symbol_requires_a_percentage_fact() -> None:
    package = _package(record_count=1)
    record = dict(package.deterministic_evidence[0])
    record["observation"] = {"percentage_change": 12.5}
    package = replace(package, deterministic_evidence=(record,))

    report = generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(
            commentary=(
                "The verified percentage change is 12.5%, which supports "
                "a descriptive comparison."
            )
        ),
    )

    assert report.stories[0].narration_source == "ollama"
    assert "12.5%" in report.stories[0].finding
    assert report.stories[0].fact_references[0].label == (
        "revenue percentage change"
    )


@pytest.mark.parametrize(
    "commentary",
    [
        "Revenue increased by 20 percent.",
        "Revenue increased by twenty percent.",
        "The verified result is 123.4.",
        "The verified result is 999.",
        "Usage drives the observed outcome.",
    ],
)
def test_untrusted_numeric_or_causal_commentary_falls_back_safely(
    commentary: str,
) -> None:
    report = generate_narrated_report(
        _package(record_count=1),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(commentary=commentary),
    )

    assert report.stories[0].narration_source == "deterministic_only"
    assert report.ai_narrated_evidence_ids == ()
    assert report.deterministic_only_evidence_ids == (
        report.items[0].evidence_id,
    )
    assert report.generation_limitations


def test_number_without_selected_fact_reference_is_rejected() -> None:
    class ReferenceFreeClient:
        def chat(self, **kwargs: object) -> object:
            schema = kwargs["format"]
            story_id = schema["properties"]["story_id"]["enum"][0]
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "story_id": story_id,
                            "headline": "A pattern merits review",
                            "finding": "The verified result is 123.45.",
                            "interpretation": (
                                "The evidence is relevant to the objective."
                            ),
                            "follow_up": (
                                "Review whether the pattern persists."
                            ),
                            "caveat": (
                                "The analysis remains descriptive."
                            ),
                            "fact_references": [],
                        }
                    )
                }
            }

    report = generate_narrated_report(
        _package(record_count=1),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=ReferenceFreeClient(),
    )

    assert report.stories[0].narration_source == "deterministic_only"
    assert report.ai_narrated_evidence_ids == ()


def test_kpi_only_report_skips_ollama() -> None:
    client = FakeClient()
    report = generate_narrated_report(
        _package(record_count=0),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=client,
    )

    assert client.calls == []
    assert report.items == ()
    assert report.generation_limitations


def test_unknown_model_evidence_reference_is_rejected() -> None:
    class UnknownEvidenceClient:
        def chat(self, **kwargs: object) -> object:
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "story_id": "STY-FFFFFFFFFFFFFFFF",
                            "headline": "A pattern merits review",
                            "finding": (
                                "This evidence supports cautious "
                                "descriptive review."
                            ),
                            "interpretation": (
                                "The evidence is relevant to the objective."
                            ),
                            "follow_up": "Review the pattern.",
                            "caveat": "The analysis is descriptive.",
                            "fact_references": [],
                        }
                    )
                }
            }

    report = generate_narrated_report(
        _package(record_count=1),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=UnknownEvidenceClient(),
    )

    assert report.ai_narrated_evidence_ids == ()
    assert report.stories[0].narration_source == "deterministic_only"


def test_one_invalid_story_does_not_discard_valid_synthesis() -> None:
    class MixedClient:
        def __init__(self) -> None:
            self.invalid_story_id: str | None = None

        def chat(self, **kwargs: object) -> object:
            schema = kwargs["format"]
            properties = schema["properties"]
            story_id = properties["story_id"]["enum"][0]
            references = properties["fact_references"]["items"]["enum"]
            if self.invalid_story_id is None:
                self.invalid_story_id = story_id
            invalid = story_id == self.invalid_story_id
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "story_id": story_id,
                            "headline": "A pattern merits review",
                            "finding": (
                                "The result was 20 percent."
                                if invalid
                                else (
                                    "This evidence supports a descriptive "
                                    "comparison."
                                )
                            ),
                            "interpretation": (
                                "The evidence is relevant to the objective."
                            ),
                            "follow_up": "Review whether the pattern persists.",
                            "caveat": "The analysis remains descriptive.",
                            "fact_references": references[:1],
                        }
                    )
                }
            }

    package = _package(record_count=4)
    records = []
    for index, original in enumerate(package.deterministic_evidence):
        record = dict(original)
        if index % 2:
            record["metric"] = "academic_performance"
            record["metric_id"] = "MET-ACADEMIC1234"
        records.append(record)
    package = replace(package, deterministic_evidence=tuple(records))
    report = generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=MixedClient(),
    )

    assert report.stories[0].narration_source == "deterministic_only"
    assert report.stories[1].narration_source == "ollama"
    assert report.stories[1].fact_references


def test_manual_visualization_evidence_receives_safe_commentary() -> None:
    manual_evidence = ManualVisualizationEvidence(
        schema_version=1,
        id="MVE-AAAAAAAAAAAAAAAA",
        visualization_id="VIS-BBBBBBBBBBBBBBBB",
        classification="supplementary",
        title="Anxiety by platform",
        purpose="Which platform has the highest anxiety?",
        purpose_source="user_provided",
        chart_type="category_bar",
        source={},
        source_columns=("platform", "anxiety"),
        required_metric_ids=(),
        measures=(
            {
                "selector": "column:anxiety",
                "label": "anxiety",
                "role": "supplementary",
            },
        ),
        filters={},
        filtered_record_count=20,
        observations=(
            {
                "type": "displayed_extremes",
                "highest": {"label": "Platform A", "value": 8.5},
            },
        ),
        supporting_data=(),
        supporting_data_omitted_count=0,
        limitations=(
            "Observations are descriptive and do not establish causation.",
        ),
    )
    package = replace(
        _package(record_count=0),
        manual_visualization_evidence=(manual_evidence,),
    )

    report = generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(),
    )

    assert len(report.items) == 1
    assert report.items[0].evidence_id == manual_evidence.id
    assert report.items[0].metric == "anxiety"
    assert report.items[0].visualization_id == (
        manual_evidence.visualization_id
    )
    assert report.stories[0].narration_source == "ollama"
    assert report.stories[0].fact_references


def test_fact_reference_prioritizes_the_top_segment_value() -> None:
    package = _package(record_count=1)
    record = dict(package.deterministic_evidence[0])
    record["observation"] = {
        "bottom_segment": {
            "record_count": 10,
            "segment": "low",
            "value": 25.0,
        },
        "top_segment": {
            "record_count": 12,
            "segment": "high",
            "value": 75.0,
        },
    }
    package = replace(
        package,
        deterministic_evidence=(record,),
    )

    report = generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(),
    )

    reference = report.stories[0].fact_references[0]
    assert reference.path == "facts.top_segment.value"
    assert reference.label == "revenue top segment value"
    assert reference.value == 75.0


def test_story_packs_are_grouped_by_metric() -> None:
    package = _package(record_count=4)
    records = []
    for index, original in enumerate(package.deterministic_evidence):
        record = dict(original)
        if index % 2:
            record["metric"] = "academic_performance"
            record["metric_id"] = "MET-ACADEMIC1234"
        records.append(record)
    package = replace(
        package,
        deterministic_evidence=tuple(records),
    )
    client = FakeClient()

    generate_narrated_report(
        package,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=client,
    )

    packs = [
        [
            reference.split("::", maxsplit=1)[0]
            for reference in call["format"]["properties"][
                "fact_references"
            ]["items"]["enum"]
        ]
        for call in client.calls
        if "story_id" in call["format"]["properties"]
    ]
    assert [list(dict.fromkeys(pack)) for pack in packs] == [
        ["EVD-0000000000000001", "EVD-0000000000000003"],
        ["EVD-0000000000000002", "EVD-0000000000000004"],
    ]


def test_generated_reports_are_immutable_versioned_and_package_bound(
    tmp_path: Path,
) -> None:
    report = generate_narrated_report(
        _package(record_count=1),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(),
    )
    first, first_path = save_generated_report(
        report,
        generated_report_dir=tmp_path,
    )
    second, second_path = save_generated_report(
        report,
        generated_report_dir=tmp_path,
    )

    assert first.version == 1
    assert second.version == 2
    assert first_path.is_file()
    assert second_path.is_file()
    assert first_path != second_path
    loaded = load_generated_report(
        report.dataset_id,
        report.report_id,
        generated_report_dir=tmp_path,
        expected_package_sha256=report.source_package_sha256,
    )
    assert loaded.version in {1, 2}
    assert loaded.executive_summary == report.executive_summary
    latest = latest_generated_report(
        report.dataset_id,
        generated_report_dir=tmp_path,
        expected_package_sha256=report.source_package_sha256,
    )
    assert latest is not None
    assert latest.version == 2
    history = list_generated_report_versions(
        report.dataset_id,
        generated_report_dir=tmp_path,
    )
    assert [item.version for item in history] == [2, 1]
    assert load_generated_report_version(
        report.dataset_id,
        report.report_id,
        1,
        generated_report_dir=tmp_path,
    ).version == 1
    chart_source = tmp_path / "source-chart.png"
    chart_source.write_bytes(b"test chart bytes")
    evidence_id = first.items[0].evidence_id
    snapshot_generated_report_charts(
        first,
        {evidence_id: chart_source},
        generated_report_asset_dir=tmp_path / "report-assets",
    )
    snapshots = generated_report_chart_snapshots(
        first,
        generated_report_asset_dir=tmp_path / "report-assets",
    )
    assert snapshots[evidence_id].read_bytes() == b"test chart bytes"
    with pytest.raises(ReportNarrationError, match="stale"):
        load_generated_report(
            report.dataset_id,
            report.report_id,
            generated_report_dir=tmp_path,
            expected_package_sha256="f" * 64,
        )

    tampered = json.loads(second_path.read_text(encoding="utf-8"))
    tampered["stories"][0]["fact_references"][0]["value"] = 999
    tampered["stories"][0]["fact_references"][0][
        "formatted_value"
    ] = "999"
    second_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ReportNarrationError, match="modified"):
        load_generated_report(
            report.dataset_id,
            report.report_id,
            generated_report_dir=tmp_path,
        )


def test_older_report_without_executive_summary_remains_publishable(
    tmp_path: Path,
) -> None:
    report = generate_narrated_report(
        _package(record_count=1),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(),
    )
    saved, path = save_generated_report(
        report,
        generated_report_dir=tmp_path,
    )
    legacy_payload = json.loads(path.read_text(encoding="utf-8"))
    legacy_payload["schema_version"] = 5
    legacy_payload.pop("executive_summary")
    path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    legacy = load_generated_report(
        saved.dataset_id,
        saved.report_id,
        generated_report_dir=tmp_path,
    )
    revised = publish_report_presentation(
        legacy,
        included_story_ids=tuple(
            story.story_id for story in legacy.stories
        ),
        story_order=tuple(
            story.story_id for story in legacy.stories
        ),
    )
    saved_revision, _path = save_generated_report(
        revised,
        generated_report_dir=tmp_path,
    )

    assert legacy.executive_summary == ()
    assert saved_revision.schema_version == 5
    assert load_generated_report(
        saved.dataset_id,
        saved.report_id,
        generated_report_dir=tmp_path,
    ).executive_summary == ()


def test_schema_six_management_summary_remains_publishable(
    tmp_path: Path,
) -> None:
    report = generate_narrated_report(
        _package(record_count=1),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=FakeClient(),
    )
    saved, path = save_generated_report(
        report,
        generated_report_dir=tmp_path,
    )
    legacy_payload = json.loads(path.read_text(encoding="utf-8"))
    legacy_payload["schema_version"] = 6
    legacy_payload.pop("generation_diagnostics")
    for point in legacy_payload["executive_summary"]:
        point.pop("business_implication")
        point.pop("recommended_action")
    path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    legacy = load_generated_report(
        saved.dataset_id,
        saved.report_id,
        generated_report_dir=tmp_path,
    )
    revised = publish_report_presentation(
        legacy,
        included_story_ids=tuple(
            story.story_id for story in legacy.stories
        ),
        story_order=tuple(
            story.story_id for story in legacy.stories
        ),
    )
    saved_revision, _path = save_generated_report(
        revised,
        generated_report_dir=tmp_path,
    )

    assert saved_revision.schema_version == 6
    assert saved_revision.generation_diagnostics == {}
    assert load_generated_report(
        saved.dataset_id,
        saved.report_id,
        generated_report_dir=tmp_path,
    ).schema_version == 6
