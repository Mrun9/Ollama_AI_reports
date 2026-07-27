"""Milestone 5A report-selection correctness and staleness tests."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from insight_reporter.business_config import validate_business_configuration
from insight_reporter.dataset_profile import profile_csv
from insight_reporter.dataset_view import CsvDatasetView
from insight_reporter.evidence_layer import generate_evidence
from insight_reporter.insight_engine import generate_insights
from insight_reporter.report_configuration import (
    ReportConfigurationError,
    load_report_configuration,
    save_report_configuration,
    validate_report_configuration,
)
from insight_reporter.visualization_builder import (
    build_visualization,
    parse_visualization_spec,
    save_visualization,
)


def _assets(tmp_path: Path):  # type: ignore[no-untyped-def]
    dataset_id = "a" * 32
    path = tmp_path / f"{dataset_id}.csv"
    path.write_text(
        (
            "date,segment,revenue,cost\n"
            "2026-01-01,North,100,60\n"
            "2026-01-02,South,200,120\n"
            "2026-02-01,North,150,80\n"
            "2026-02-02,South,250,140\n"
            "2026-03-01,North,180,90\n"
            "2026-03-02,South,300,160\n"
        ),
        encoding="utf-8",
    )
    view = CsvDatasetView.from_path(path)
    profile = profile_csv(path)
    configuration = validate_business_configuration(
        profile,
        dataset_id=dataset_id,
        primary_kpi="revenue",
        secondary_kpis=["cost"],
        kpi_direction="higher",
        date_column="date",
        category_columns=["segment"],
        target_or_benchmark="",
        business_objective="Review revenue and cost.",
    )
    insights = generate_insights(
        view,
        profile=profile,
        configuration=configuration,
    )
    evidence = generate_evidence(
        view,
        profile=profile,
        configuration=configuration,
        insight_report=insights,
        chart_dir=tmp_path / "charts",
    ).to_dict()
    visualization = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=parse_visualization_spec(
            {
                "title": "Revenue by segment",
                "chart_type": "category_bar",
                "measure_selectors": [
                    f"metric:{configuration.primary_metric_id}"
                ],
                "x_column": "segment",
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
        ),
        chart_dir=tmp_path / "charts",
    )
    saved_visualization, _path = save_visualization(
        visualization,
        visualization_dir=tmp_path / "visualizations",
    )
    return configuration, evidence, (saved_visualization,)


def _validated_report(
    configuration,  # type: ignore[no-untyped-def]
    evidence,  # type: ignore[no-untyped-def]
    visualizations,  # type: ignore[no-untyped-def]
    **overrides,  # type: ignore[no-untyped-def]
):  # type: ignore[no-untyped-def]
    values = {
        "title": "Quarterly performance report",
        "business_objective": "Review revenue and cost performance.",
        "audience": "management",
        "tone": "professional",
        "detail_level": "standard",
        "user_notes": "Prepared for the operating review.",
        "include_evidence_appendix": "yes",
        "selected_metric_ids": [
            metric.metric_id for metric in configuration.metrics
        ],
        "selected_evidence_ids": [
            record["id"] for record in evidence["records"][:3]
        ],
        "selected_visualization_ids": [
            visualizations[0].visualization_id
        ],
    }
    values.update(overrides)
    return validate_report_configuration(
        configuration,
        evidence_payload=evidence,
        visualizations=visualizations,
        **values,
    )


def test_report_selection_is_ordered_traceable_and_round_trips(
    tmp_path: Path,
) -> None:
    configuration, evidence, visualizations = _assets(tmp_path)
    report = _validated_report(configuration, evidence, visualizations)
    path = save_report_configuration(
        report,
        report_configuration_dir=tmp_path / "reports",
    )
    loaded = load_report_configuration(
        path,
        configuration=configuration,
        evidence_payload=evidence,
        visualizations=visualizations,
    )

    assert loaded == report
    assert loaded.schema_version == 2
    assert loaded.sources == tuple(
        source.to_dict() for source in configuration.sources
    )
    assert len(loaded.business_configuration_sha256) == 64
    assert loaded.evidence_sha256 is not None
    assert len(loaded.evidence_sha256) == 64
    assert set(loaded.visualization_sha256s) == set(
        loaded.selected_visualization_ids
    )
    selected_ranks = {
        record["id"]: record["ranking"]["rank"]
        for record in evidence["records"]
        if record["id"] in loaded.selected_evidence_ids
    }
    assert list(loaded.selected_evidence_ids) == sorted(
        loaded.selected_evidence_ids,
        key=lambda evidence_id: selected_ranks[evidence_id],
    )


def test_previous_report_configuration_loads_with_empty_branding(
    tmp_path: Path,
) -> None:
    configuration, evidence, visualizations = _assets(tmp_path)
    report = _validated_report(configuration, evidence, visualizations)
    path = save_report_configuration(
        report,
        report_configuration_dir=tmp_path / "reports",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    payload.pop("company_name")
    payload.pop("report_author")
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_report_configuration(
        path,
        configuration=configuration,
        evidence_payload=evidence,
        visualizations=visualizations,
    )

    assert loaded.schema_version == 2
    assert loaded.company_name == ""
    assert loaded.report_author == ""


def test_kpi_only_report_works_without_evidence_or_visualizations(
    tmp_path: Path,
) -> None:
    configuration, _evidence, _visualizations = _assets(tmp_path)
    report = validate_report_configuration(
        configuration,
        evidence_payload=None,
        visualizations=(),
        title="KPI definitions",
        business_objective="Document the selected KPI.",
        audience="general",
        tone="concise",
        detail_level="brief",
        user_notes="",
        include_evidence_appendix=False,
        selected_metric_ids=[configuration.primary_metric_id],
        selected_evidence_ids=[],
        selected_visualization_ids=[],
    )

    assert report.selected_metric_ids == (
        configuration.primary_metric_id,
    )
    assert report.selected_evidence_ids == ()
    assert report.selected_visualization_ids == ()
    assert report.evidence_sha256 is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"selected_metric_ids": []}, "at least one KPI"),
        ({"selected_metric_ids": ["MET-NOT-AVAILABLE"]}, "configured KPIs"),
        ({"selected_evidence_ids": ["EVD-NOT-AVAILABLE"]}, "unavailable"),
        (
            {"selected_visualization_ids": ["VIS-NOT-AVAILABLE"]},
            "marked for report inclusion",
        ),
        ({"audience": "everyone"}, "audience"),
        ({"tone": "causal"}, "tone"),
        ({"detail_level": "unlimited"}, "detail level"),
        ({"title": " "}, "title is required"),
        ({"user_notes": "x" * 5_001}, "at most 5000"),
    ],
)
def test_unknown_or_unbounded_selections_are_rejected(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    configuration, evidence, visualizations = _assets(tmp_path)

    with pytest.raises(ReportConfigurationError, match=message):
        _validated_report(
            configuration,
            evidence,
            visualizations,
            **overrides,
        )


def test_evidence_requires_its_kpi_to_be_selected(tmp_path: Path) -> None:
    configuration, evidence, visualizations = _assets(tmp_path)
    cost = next(
        metric for metric in configuration.metrics if metric.name == "cost"
    )
    cost_evidence = next(
        record
        for record in evidence["records"]
        if record["metric_id"] == cost.metric_id
    )

    with pytest.raises(ReportConfigurationError, match="selected KPI"):
        _validated_report(
            configuration,
            evidence,
            visualizations,
            selected_metric_ids=[configuration.primary_metric_id],
            selected_evidence_ids=[cost_evidence["id"]],
            selected_visualization_ids=[],
        )


def test_kpi_visualization_names_the_missing_report_kpi(
    tmp_path: Path,
) -> None:
    configuration, evidence, visualizations = _assets(tmp_path)
    cost = next(
        metric for metric in configuration.metrics if metric.name == "cost"
    )

    with pytest.raises(
        ReportConfigurationError,
        match='Visualization "Revenue by segment" requires these report KPIs: revenue',
    ):
        _validated_report(
            configuration,
            evidence,
            visualizations,
            selected_metric_ids=[cost.metric_id],
            selected_evidence_ids=[],
        )


def test_saved_selection_rejects_changed_configuration_evidence_and_chart(
    tmp_path: Path,
) -> None:
    configuration, evidence, visualizations = _assets(tmp_path)
    report = _validated_report(configuration, evidence, visualizations)
    path = save_report_configuration(
        report,
        report_configuration_dir=tmp_path / "reports",
    )

    with pytest.raises(ReportConfigurationError, match="stale"):
        load_report_configuration(
            path,
            configuration=replace(
                configuration,
                business_objective="Changed objective.",
            ),
            evidence_payload=evidence,
            visualizations=visualizations,
        )

    changed_evidence = {
        **evidence,
        "records": list(reversed(evidence["records"])),
    }
    with pytest.raises(ReportConfigurationError, match="stale"):
        load_report_configuration(
            path,
            configuration=configuration,
            evidence_payload=changed_evidence,
            visualizations=visualizations,
        )

    changed_visualization = replace(
        visualizations[0],
        created_at="2099-01-01T00:00:00+00:00",
    )
    with pytest.raises(ReportConfigurationError, match="stale"):
        load_report_configuration(
            path,
            configuration=configuration,
            evidence_payload=evidence,
            visualizations=(changed_visualization,),
        )
