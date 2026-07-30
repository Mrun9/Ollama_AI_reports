"""Milestone 4A evidence traceability, ranking, and chart correctness tests."""

import json
import math
import re
from pathlib import Path

import pytest

from insight_reporter.business_config import validate_business_configuration
from insight_reporter.dataset_profile import profile_csv
from insight_reporter.dataset_view import CsvDatasetView
from insight_reporter.evidence_layer import (
    chart_filename_for,
    delete_chart_files,
    generate_evidence,
    load_evidence_payload,
    referenced_chart_filenames,
    save_evidence_report,
)
from insight_reporter.insight_engine import generate_insights


def _evidence_fixture(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "dataset.csv"
    path.write_text(
        (
            "date,segment,revenue,cost,discount\n"
            "2026-01-01,<script>alert(1)</script>,10,20,1\n"
            "2026-01-02,<script>alert(1)</script>,11,22,1\n"
            "2026-01-03,B,12,24,1\n"
            "2026-01-04,B,13,26,\n"
            "2026-02-01,<script>alert(1)</script>,14,28,1\n"
            "2026-02-02,<script>alert(1)</script>,15,30,1\n"
            "2026-02-03,B,16,32,1\n"
            "2026-02-04,B,17,34,1\n"
            "2026-03-01,<script>alert(1)</script>,18,36,1\n"
            "2026-03-02,<script>alert(1)</script>,19,38,1\n"
            "2026-03-03,B,20,40,1\n"
            "2026-03-04,B,200,400,1\n"
        ),
        encoding="utf-8",
    )
    view = CsvDatasetView.from_path(path)
    profile = profile_csv(path)
    configuration = validate_business_configuration(
        profile,
        dataset_id="a" * 32,
        primary_kpi="revenue",
        kpi_direction="higher",
        date_column="date",
        category_columns=["segment"],
        target_or_benchmark="50",
        business_objective="Review revenue evidence.",
    )
    insights = generate_insights(
        view,
        profile=profile,
        configuration=configuration,
    )
    return view, profile, configuration, insights


def test_every_insight_has_stable_ranked_traceable_evidence(tmp_path: Path) -> None:
    view, profile, configuration, insights = _evidence_fixture(tmp_path)

    first = generate_evidence(
        view,
        profile=profile,
        configuration=configuration,
        insight_report=insights,
        chart_dir=tmp_path / "charts-one",
    )
    second = generate_evidence(
        view,
        profile=profile,
        configuration=configuration,
        insight_report=insights,
        chart_dir=tmp_path / "charts-two",
    )

    assert len(first.records) == len(insights.insights)
    assert {record.insight_id for record in first.records} == {
        insight.id for insight in insights.insights
    }
    assert [record.id for record in first.records] == [
        record.id for record in second.records
    ]
    assert [record.ranking for record in first.records] == [
        record.ranking for record in second.records
    ]
    assert sorted(record.ranking.rank for record in first.records) == list(
        range(1, len(first.records) + 1)
    )
    for record in first.records:
        assert re.fullmatch(r"EVD-[0-9A-F]{16}", record.id)
        assert record.source == {
            "source_id": view.sources[0].source_id,
            "filename": "dataset.csv",
            "format": "csv",
            "sha256": profile.source_sha256,
            "worksheet": None,
        }
        assert record.calculation_description
        assert record.supporting_data
        assert set(record.source_columns).issubset(view.headers)
        assert 0 <= record.ranking.impact <= 1
        assert 0 <= record.ranking.confidence <= 1
        assert 0 <= record.ranking.relevance <= 1
        assert 0 <= record.ranking.combined <= 1


def test_all_initial_chart_types_are_secure_and_use_supporting_data(
    tmp_path: Path,
) -> None:
    view, profile, configuration, insights = _evidence_fixture(tmp_path)
    chart_dir = tmp_path / "charts"
    evidence = generate_evidence(
        view,
        profile=profile,
        configuration=configuration,
        insight_report=insights,
        chart_dir=chart_dir,
    )

    charts = [record.chart for record in evidence.records if record.chart is not None]
    assert {chart.chart_type for chart in charts} == {
        "time_trend",
        "period_baseline_comparison",
        "category_comparison",
        "category_share",
        "cohort_period_comparison",
        "segment_contribution",
        "segment_target_performance",
        "distribution_iqr_outliers",
        "missing_data_overview",
    }
    for chart in charts:
        assert re.fullmatch(r"[0-9a-f]{32}\.png", chart.filename)
        path = (chart_dir / chart.filename).resolve()
        assert path.parent == chart_dir.resolve()
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert chart.record_count > 0
        assert len(chart.title) <= 60
        assert len(chart.alt_text) <= 60
        record = next(item for item in evidence.records if item.chart == chart)
        assert set(chart.data_columns).issubset(record.supporting_data[0])


def test_management_evidence_outranks_correlation(
    tmp_path: Path,
) -> None:
    view, profile, configuration, insights = _evidence_fixture(tmp_path)
    evidence = generate_evidence(
        view,
        profile=profile,
        configuration=configuration,
        insight_report=insights,
        chart_dir=tmp_path / "charts",
    )
    by_type = {record.insight_type: record for record in evidence.records}

    assert (
        by_type["segment_benchmark_performance"].ranking.rank
        < by_type["numeric_correlation"].ranking.rank
    )
    assert (
        by_type["period_baseline_comparison"].ranking.rank
        < by_type["numeric_correlation"].ranking.rank
    )
    assert (
        by_type["cohort_period_comparison"].ranking.rank
        < by_type["numeric_correlation"].ranking.rank
    )


def test_supporting_values_reproduce_period_correlation_and_outliers(
    tmp_path: Path,
) -> None:
    view, profile, configuration, insights = _evidence_fixture(tmp_path)
    evidence = generate_evidence(
        view,
        profile=profile,
        configuration=configuration,
        insight_report=insights,
        chart_dir=tmp_path / "charts",
    )
    by_type = {record.insight_type: record for record in evidence.records}

    period = by_type["period_change"]
    previous, current = period.supporting_data
    assert float(current["value"]) - float(previous["value"]) == 195

    baseline = by_type["period_baseline_comparison"]
    assert [row["role"] for row in baseline.supporting_data] == [
        "baseline",
        "baseline",
        "current",
    ]
    cohort = by_type["cohort_period_comparison"]
    assert {
        row["cohort"] for row in cohort.supporting_data
    } == {"<script>alert(1)</script>", "B"}

    correlation = by_type["numeric_correlation"].supporting_data[0]
    count = int(correlation["pair_count"])
    numerator = (
        count * float(correlation["sum_xy"])
        - float(correlation["sum_x"]) * float(correlation["sum_y"])
    )
    denominator = math.sqrt(
        (
            count * float(correlation["sum_x_squared"])
            - float(correlation["sum_x"]) ** 2
        )
        * (
            count * float(correlation["sum_y_squared"])
            - float(correlation["sum_y"]) ** 2
        )
    )
    assert numerator / denominator == pytest.approx(float(correlation["coefficient"]))

    outliers = by_type["iqr_anomaly_detection"].supporting_data
    assert [row for row in outliers if row["is_outlier"]] == [
        {"row_number": 13, "value": 200.0, "is_outlier": True}
    ]


def test_evidence_persistence_and_chart_cleanup_accept_safe_names_only(
    tmp_path: Path,
) -> None:
    view, profile, configuration, insights = _evidence_fixture(tmp_path)
    chart_dir = tmp_path / "charts"
    evidence = generate_evidence(
        view,
        profile=profile,
        configuration=configuration,
        insight_report=insights,
        chart_dir=chart_dir,
    )
    path = save_evidence_report(evidence, evidence_dir=tmp_path / "evidence")
    payload = load_evidence_payload(path, dataset_id="a" * 32)
    chart_record = next(
        record for record in payload["records"] if record["chart"] is not None
    )

    assert chart_filename_for(
        payload,
        evidence_id=chart_record["id"],
    ) == chart_record["chart"]["filename"]
    assert chart_filename_for(payload, evidence_id="../../escape") is None
    filenames = referenced_chart_filenames(payload)
    delete_chart_files(chart_dir, (*filenames, "../../do-not-delete"))
    assert not any((chart_dir / filename).exists() for filename in filenames)
    assert json.loads(path.read_text(encoding="utf-8"))["dataset_id"] == "a" * 32


def test_multiple_configured_kpis_receive_separate_evidence(tmp_path: Path) -> None:
    view, profile, _, _ = _evidence_fixture(tmp_path)
    configuration = validate_business_configuration(
        profile,
        dataset_id="a" * 32,
        primary_kpi="revenue",
        secondary_kpis=["cost"],
        kpi_direction="higher",
        date_column="date",
        category_columns=["segment"],
        target_or_benchmark="",
        business_objective="Compare revenue and cost evidence.",
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
    )

    configured_ids = {metric.metric_id for metric in configuration.metrics}
    evidence_ids = {
        record.metric_id for record in evidence.records if record.metric_id != "DATASET"
    }
    assert evidence_ids == configured_ids
    assert {record.metric for record in evidence.records} >= {"revenue", "cost"}
