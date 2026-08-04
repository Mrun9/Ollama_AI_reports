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
    generate_visualization_insight,
    load_visualization_insight,
    save_visualization_insight,
    set_visualization_insight_report_inclusion,
)


class _GroundedInsightClient:
    def chat(self, **kwargs: object) -> object:
        messages = kwargs["messages"]
        payload = json.loads(messages[1]["content"])
        return {
            "message": {
                "content": json.dumps(
                    {
                        "insights": [
                            {
                                "fact_id": fact["fact_id"],
                                "implication": (
                                    "This result identifies where management "
                                    "attention may have the greatest impact."
                                ),
                                "suggested_action": (
                                    "Review the underlying operating drivers "
                                    "and assign an owner for follow-up."
                                ),
                            }
                            for fact in payload["facts"]
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

    assert 1 <= len(insight.points) <= 5
    assert insight.model_status == "generated"
    assert insight.include_in_reports is True
    assert any("South" in point.finding and "North" in point.finding for point in insight.points)
    assert any("550" in point.finding for point in insight.points)
    assert all(point.implication for point in insight.points)
    assert all(point.suggested_action for point in insight.points)


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
        item for item in observations if item["type"] == "user_requested_visualization_insight"
    ]
    assert len(requested) == len(insight.points)
    assert requested[0]["observation"]["question"] == ("Where should management focus?")
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
        item["type"] != "user_requested_visualization_insight"
        for item in excluded.manual_visualization_evidence[0].observations
    )
    assert excluded.omissions["included_visualization_insight_count"] == 0
