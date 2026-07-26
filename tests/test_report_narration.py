"""Milestone 5B.1 synthesis trust-boundary and persistence tests."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from insight_reporter.manual_visualization_evidence import (
    ManualVisualizationEvidence,
)
from insight_reporter.report_generation_package import (
    ReportGenerationPackage,
)
from insight_reporter.report_narration import (
    ReportNarrationError,
    generate_narrated_report,
    latest_generated_report,
    load_generated_report,
    save_generated_report,
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


def test_narration_synthesizes_story_packs_and_is_traceable() -> None:
    client = FakeClient()

    report = generate_narrated_report(
        _package(),
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=120,
        client=client,
    )

    assert len(client.calls) == 2
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
    assert report.schema_version == 3
    assert report.items[0].facts == {
        "highest_segment": "North",
        "value": 123.45,
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
    assert client.calls[0]["think"] is False
    assert client.calls[0]["options"]["num_ctx"] == 4_096


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
            self.call_count = 0

        def chat(self, **kwargs: object) -> object:
            schema = kwargs["format"]
            properties = schema["properties"]
            story_id = properties["story_id"]["enum"][0]
            references = properties["fact_references"]["items"]["enum"]
            invalid = self.call_count == 0
            self.call_count += 1
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
    latest = latest_generated_report(
        report.dataset_id,
        generated_report_dir=tmp_path,
        expected_package_sha256=report.source_package_sha256,
    )
    assert latest is not None
    assert latest.version == 2
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
