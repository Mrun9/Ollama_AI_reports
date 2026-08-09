"""Grounding, persistence, and report carry-forward tests for chart insights."""

import json
from pathlib import Path

from insight_reporter.business_config import validate_business_configuration
from insight_reporter.dataset_profile import profile_csv
from insight_reporter.dataset_view import CsvDatasetView
from insight_reporter.manual_visualization_store import ManualVisualizationArtifact
from insight_reporter.report_configuration import validate_report_configuration
from insight_reporter.report_generation_package import (
    build_report_generation_package,
)
from insight_reporter.visualization_builder import (
    build_visualization,
    parse_visualization_spec,
    save_visualization,
)
from insight_reporter.visualization_insights import (
    build_verified_visualization_observations,
    generate_visualization_insight,
    load_visualization_insight,
    save_visualization_insight,
    set_visualization_insight_report_inclusion,
)


class _GroundedInsightClient:
    def chat(self, **kwargs: object) -> object:
        messages = kwargs["messages"]
        payload = json.loads(messages[1]["content"])
        answer_texts = (
            "The cited comparison directly answers the first chart question.",
            "The cited total directly answers the second chart question.",
            "The cited change directly answers the third chart question.",
            "The cited distribution directly answers the fourth chart question.",
            "The cited ranking directly answers the fifth chart question.",
        )
        return {
            "message": {
                "content": json.dumps(
                    {
                        "answers": [
                            {
                                "question_id": question["question_id"],
                                "status": "answered",
                                "answer": answer_texts[index],
                                "supporting_fact_ids": [
                                    payload["facts"][index % len(payload["facts"])]["fact_id"]
                                ],
                                "suggested_action": (
                                    "Review the underlying operating drivers "
                                    "and assign an owner for follow-up."
                                ),
                            }
                            for index, question in enumerate(payload["questions"])
                        ]
                    }
                )
            }
        }


class _InsufficientEvidenceClient:
    def chat(self, **kwargs: object) -> object:
        messages = kwargs["messages"]
        payload = json.loads(messages[1]["content"])
        return {
            "message": {
                "content": json.dumps(
                    {
                        "answers": [
                            {
                                "question_id": question["question_id"],
                                "status": "insufficient_evidence",
                                "answer": (
                                    "The saved chart does not contain the data needed "
                                    "to answer this question."
                                ),
                                "supporting_fact_ids": [],
                                "suggested_action": "",
                            }
                            for question in payload["questions"]
                        ]
                    }
                )
            }
        }


def _saved_chart(tmp_path: Path):  # type: ignore[no-untyped-def]
    dataset_id = "e" * 32
    path = tmp_path / f"{dataset_id}.csv"
    path.write_text(
        "date,region,revenue\n"
        "2026-01-01,North,100\n"
        "2026-01-02,South,250\n"
        "2026-02-01,North,150\n"
        "2026-02-02,South,300\n",
        encoding="utf-8",
    )
    view = CsvDatasetView.from_path(path)
    profile = profile_csv(path)
    configuration = validate_business_configuration(
        profile,
        dataset_id=dataset_id,
        primary_kpi="revenue",
        kpi_direction="higher",
        date_column="date",
        category_columns=["region"],
        target_or_benchmark="",
        business_objective="Understand regional revenue.",
    )
    spec = parse_visualization_spec(
        {
            "title": "Revenue by region",
            "purpose": "Where should management focus?",
            "chart_type": "category_bar",
            "measure_selectors": [f"metric:{configuration.primary_metric_id}"],
            "x_column": "region",
            "series_column": "",
            "aggregation": "sum",
            "date_granularity": "month",
            "filter_column": "",
            "filter_mode": "include",
            "filter_values": "",
            "date_start": "",
            "date_end": "",
            "sort_by": "value",
            "sort_direction": "descending",
            "top_n": "10",
            "scale": "linear",
            "bin_count": "10",
            "include_in_report": "yes",
            "replaces_visualization_id": "",
        }
    )
    chart = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=spec,
        chart_dir=tmp_path / "charts",
        dataset_id=dataset_id,
    )
    saved, _path = save_visualization(
        chart,
        visualization_dir=tmp_path / "visualizations",
    )
    return saved, profile, configuration


def test_saved_chart_insight_uses_values_and_grounded_model_text(
    tmp_path: Path,
) -> None:
    chart, _profile, _configuration = _saved_chart(tmp_path)

    insight = generate_visualization_insight(
        chart,
        question="Which region requires management attention?",
        include_in_reports=True,
        use_model=True,
        model="test-model",
        host="http://unused",
        timeout_seconds=1,
        metrics_dir=tmp_path / "metrics",
        client=_GroundedInsightClient(),
    )

    assert 1 <= len(insight.facts) <= 5
    assert len(insight.answers) == 1
    assert insight.model_status == "generated"
    assert insight.include_in_reports is True
    assert any("South" in point.finding and "North" in point.finding for point in insight.points)
    assert any("550" in point.finding for point in insight.points)
    assert insight.answers[0].supporting_fact_ids
    assert insight.answers[0].answer
    assert insight.answers[0].suggested_action


def test_visualization_insight_answers_each_question_without_annotating_every_fact(
    tmp_path: Path,
) -> None:
    chart, _profile, _configuration = _saved_chart(tmp_path)

    insight = generate_visualization_insight(
        chart,
        question=(
            "Which region has the higher displayed revenue?\n"
            "What total is represented in the chart?"
        ),
        include_in_reports=False,
        use_model=True,
        model="test-model",
        host="http://unused",
        timeout_seconds=1,
        client=_GroundedInsightClient(),
    )

    assert insight.questions == (
        "Which region has the higher displayed revenue?",
        "What total is represented in the chart?",
    )
    assert len(insight.answers) == 2
    assert {answer.question_id for answer in insight.answers} == {"Q1", "Q2"}
    assert all(len(answer.supporting_fact_ids) == 1 for answer in insight.answers)


def test_visualization_insight_marks_question_outside_chart_as_unsupported(
    tmp_path: Path,
) -> None:
    chart, _profile, _configuration = _saved_chart(tmp_path)

    insight = generate_visualization_insight(
        chart,
        question="Which customer submitted the most support tickets?",
        include_in_reports=False,
        use_model=True,
        model="test-model",
        host="http://unused",
        timeout_seconds=1,
        client=_InsufficientEvidenceClient(),
    )

    assert insight.model_status == "generated"
    assert insight.answers[0].status == "insufficient_evidence"
    assert insight.answers[0].supporting_fact_ids == ()
    assert insight.answers[0].suggested_action == ""


def test_verified_visualization_observations_do_not_use_ollama(
    tmp_path: Path,
) -> None:
    chart, _profile, _configuration = _saved_chart(tmp_path)

    observations = build_verified_visualization_observations(
        chart,
        include_in_reports=True,
    )

    assert observations.include_in_reports is True
    assert observations.model_status == "not_requested"
    assert observations.model is None
    assert observations.answers == ()
    assert observations.facts


def test_manual_board_insight_uses_saved_points_and_round_trips(
    tmp_path: Path,
) -> None:
    artifact = ManualVisualizationArtifact(
        schema_version=1,
        visualization_id="MBV-AAAAAAAAAAAAAAAA",
        dataset_id="e" * 32,
        title="Revenue by region board",
        requested_chart="column",
        chart_type="column",
        fields={
            "x": "region",
            "y": "revenue",
            "series": None,
            "size": None,
            "secondary_y": None,
        },
        settings={"pareto_line": "cumulative_percent", "target": None},
        preview={
            "chart_type": "column",
            "x_label": "region",
            "y_label": "revenue",
            "aggregation": "Sum of revenue",
            "record_count": 4,
            "truncated": False,
            "points": [
                {"x": "North", "y": 250.0},
                {"x": "South", "y": 550.0},
            ],
        },
        source_sha256="f" * 64,
        svg_filename="MBV-AAAAAAAAAAAAAAAA.svg",
        png_filename="MBV-AAAAAAAAAAAAAAAA.png",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    insight = generate_visualization_insight(
        artifact,
        question="Which region should management review?",
        include_in_reports=True,
        use_model=True,
        model="test-model",
        host="http://unused",
        timeout_seconds=1,
        client=_GroundedInsightClient(),
    )

    assert insight.visualization_id == artifact.visualization_id
    assert insight.model_status == "generated"
    assert insight.include_in_reports is True
    assert any(
        "South" in point.finding and "North" in point.finding
        for point in insight.points
    )
    insight_dir = tmp_path / "visualization_insights"
    save_visualization_insight(insight, insight_dir=insight_dir)
    assert load_visualization_insight(artifact, insight_dir=insight_dir) == insight


def test_visualization_insight_persists_and_stale_hash_is_not_loaded(
    tmp_path: Path,
) -> None:
    chart, _profile, _configuration = _saved_chart(tmp_path)
    insight = generate_visualization_insight(
        chart,
        question="What does this chart show?",
        include_in_reports=False,
        use_model=False,
        model="unused",
        host="http://unused",
        timeout_seconds=1,
    )
    insight_dir = tmp_path / "visualization_insights"
    path = save_visualization_insight(insight, insight_dir=insight_dir)

    assert path.is_file()
    assert load_visualization_insight(chart, insight_dir=insight_dir) == insight

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["visualization_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_visualization_insight(chart, insight_dir=insight_dir) is None


def test_legacy_visualization_insight_still_loads_as_question_oriented_artifact(
    tmp_path: Path,
) -> None:
    chart, _profile, _configuration = _saved_chart(tmp_path)
    current = generate_visualization_insight(
        chart,
        question="What does this chart show?",
        include_in_reports=False,
        use_model=False,
        model="unused",
        host="http://unused",
        timeout_seconds=1,
    )
    legacy_payload = {
        "schema_version": 1,
        "insight_id": current.insight_id,
        "dataset_id": current.dataset_id,
        "visualization_id": current.visualization_id,
        "visualization_sha256": current.visualization_sha256,
        "generated_at": current.generated_at,
        "question": "What does this chart show?",
        "include_in_reports": False,
        "model": None,
        "model_status": "not_requested",
        "prompt_version": None,
        "points": [
            {
                "fact_id": fact.fact_id,
                "finding": fact.finding,
                "implication": "",
                "suggested_action": "",
                "interpretation_source": "python_only",
            }
            for fact in current.facts
        ],
        "limitations": list(current.limitations),
    }
    insight_dir = tmp_path / "visualization_insights"
    path = insight_dir / current.dataset_id / f"{current.visualization_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    loaded = load_visualization_insight(chart, insight_dir=insight_dir)

    assert loaded is not None
    assert loaded.schema_version == 2
    assert loaded.questions == ("What does this chart show?",)
    assert loaded.facts == current.facts


def test_opted_in_chart_insight_is_carried_into_report_evidence(
    tmp_path: Path,
) -> None:
    chart, _profile, configuration = _saved_chart(tmp_path)
    insight = generate_visualization_insight(
        chart,
        question="Where should management focus?",
        include_in_reports=True,
        use_model=False,
        model="unused",
        host="http://unused",
        timeout_seconds=1,
    )
    report = validate_report_configuration(
        configuration,
        evidence_payload=None,
        visualizations=(chart,),
        title="Regional review",
        company_name="",
        report_author="",
        business_objective="Review regional performance.",
        audience="management",
        tone="professional",
        detail_level="standard",
        user_notes="",
        include_evidence_appendix="",
        selected_metric_ids=[configuration.primary_metric_id],
        selected_evidence_ids=[],
        selected_visualization_ids=[chart.visualization_id or ""],
    )

    included = build_report_generation_package(
        report,
        configuration=configuration,
        evidence_payload=None,
        visualizations=(chart,),
        visualization_insights=(insight,),
    )
    observations = included.manual_visualization_evidence[0].observations
    requested = [
        item for item in observations if item["type"] == "verified_visualization_observation"
    ]
    assert len(requested) == len(insight.points)
    assert requested[0]["observation"]["finding"]
    assert "question" not in requested[0]["observation"]
    assert included.omissions["included_visualization_insight_count"] == 1

    excluded_insight = set_visualization_insight_report_inclusion(
        insight,
        include_in_reports=False,
    )
    excluded = build_report_generation_package(
        report,
        configuration=configuration,
        evidence_payload=None,
        visualizations=(chart,),
        visualization_insights=(excluded_insight,),
    )
    assert all(
        item["type"] != "verified_visualization_observation"
        for item in excluded.manual_visualization_evidence[0].observations
    )
    assert excluded.omissions["included_visualization_insight_count"] == 0
