"""Hand-calculated correctness tests for the deterministic insight engine."""

import json
import math
from pathlib import Path

import pytest

from insight_reporter.business_config import (
    BusinessConfiguration,
    validate_business_configuration,
    validate_derived_business_configuration,
)
from insight_reporter.dataset_profile import DatasetProfile, profile_csv
from insight_reporter.derived_metrics import validate_derived_metric
from insight_reporter.insight_engine import generate_insights, save_insight_report


def _configured_dataset(
    tmp_path: Path,
    content: str,
    *,
    primary_kpi: str = "revenue",
    direction: str = "higher",
    date_column: str = "date",
    categories: list[str] | None = None,
    target: str = "",
) -> tuple[Path, DatasetProfile, BusinessConfiguration]:
    path = tmp_path / "dataset.csv"
    path.write_text(content, encoding="utf-8")
    profile = profile_csv(path)
    configuration = validate_business_configuration(
        profile,
        dataset_id="a" * 32,
        primary_kpi=primary_kpi,
        kpi_direction=direction,
        date_column=date_column,
        category_columns=categories or [],
        target_or_benchmark=target,
        business_objective="Evaluate the configured KPI using deterministic evidence.",
    )
    return path, profile, configuration


def _main_dataset(
    tmp_path: Path, *, target: str = "20"
) -> tuple[Path, DatasetProfile, BusinessConfiguration]:
    return _configured_dataset(
        tmp_path,
        (
            "date,segment,revenue,cost,constant\n"
            "2026-01-02,A,10,20,1\n"
            "2026-01-03,A,10,20,1\n"
            "2026-01-04,B,5,10,1\n"
            "2026-01-05,B,5,10,1\n"
            "2026-02-02,A,20,40,1\n"
            "2026-02-03,A,20,40,1\n"
            "2026-02-04,B,10,20,1\n"
            "2026-02-05,B,10,20,1\n"
            "2026-03-02,A,30,60,1\n"
            "2026-03-03,A,30,60,1\n"
            "2026-03-04,B,15,30,1\n"
            "2026-03-05,B,25,50,1\n"
        ),
        categories=["segment"],
        target=target,
    )


def _one(report, insight_type: str):  # type: ignore[no-untyped-def]
    matches = [insight for insight in report.insights if insight.type == insight_type]
    assert len(matches) == 1
    return matches[0]


def test_period_percentage_and_trend_match_manual_values(tmp_path: Path) -> None:
    path, profile, configuration = _main_dataset(tmp_path)

    report = generate_insights(path, profile=profile, configuration=configuration)
    period = _one(report, "period_change")
    trend = _one(report, "trend")

    assert period.observation["previous_period"] == "2026-02"
    assert period.observation["previous_value"] == 60
    assert period.observation["current_period"] == "2026-03"
    assert period.observation["current_value"] == 100
    assert period.observation["absolute_change"] == 40
    assert period.observation["percentage_change"] == pytest.approx(200 / 3)
    assert period.observation["direction"] == "increasing"
    assert period.observation["favorable"] is True
    assert trend.observation["slope_per_period"] == 35
    assert trend.observation["direction"] == "increasing"


def test_segment_ranking_and_contributions_are_exact(tmp_path: Path) -> None:
    path, profile, configuration = _main_dataset(tmp_path)

    report = generate_insights(path, profile=profile, configuration=configuration)
    ranking = _one(report, "segment_ranking").observation
    contribution = _one(report, "segment_contribution").observation

    assert ranking["top_segment"] == {"segment": "A", "value": 120.0, "record_count": 6}
    assert ranking["bottom_segment"] == {"segment": "B", "value": 70.0, "record_count": 6}
    assert contribution["overall_change"] == 40
    assert contribution["reconciled_percentage_total"] == 100
    by_segment = {
        item["segment"]: item for item in contribution["contributions"]  # type: ignore[index]
    }
    assert by_segment["A"]["absolute_change"] == 20
    assert by_segment["A"]["contribution_percentage"] == 50
    assert by_segment["B"]["absolute_change"] == 20
    assert by_segment["B"]["contribution_percentage"] == 50


def test_correlation_is_association_and_constant_column_is_skipped(tmp_path: Path) -> None:
    path, profile, configuration = _main_dataset(tmp_path)

    report = generate_insights(path, profile=profile, configuration=configuration)
    correlations = [
        insight for insight in report.insights if insight.type == "numeric_correlation"
    ]

    assert len(correlations) == 1
    assert correlations[0].source_columns == ("revenue", "cost")
    assert correlations[0].observation["coefficient"] == 1
    assert correlations[0].observation["relationship_label"] == "association"
    assert "causation" in correlations[0].limitations[0]
    assert all("constant" not in insight.source_columns for insight in correlations)


def test_benchmark_breach_percentage_is_calculated_in_python(tmp_path: Path) -> None:
    path, profile, configuration = _main_dataset(tmp_path)

    report = generate_insights(path, profile=profile, configuration=configuration)
    breach = _one(report, "benchmark_breach")

    assert breach.record_count == 12
    assert breach.observation["breach_condition"] == "value < target"
    assert breach.observation["breach_count"] == 7
    assert breach.observation["breach_percentage"] == pytest.approx(700 / 12)


def test_lower_is_better_benchmark_uses_the_correct_breach_direction(
    tmp_path: Path,
) -> None:
    path, profile, configuration = _configured_dataset(
        tmp_path,
        "segment,revenue\nA,10\nA,20\nB,30\nB,40\nB,50\n",
        direction="lower",
        date_column="",
        target="30",
    )

    report = generate_insights(path, profile=profile, configuration=configuration)
    breach = _one(report, "benchmark_breach")

    assert breach.observation["breach_condition"] == "value > target"
    assert breach.observation["breach_count"] == 2
    assert breach.observation["breach_percentage"] == 40


def test_iqr_anomaly_matches_known_outlier(tmp_path: Path) -> None:
    path, profile, configuration = _configured_dataset(
        tmp_path,
        "segment,revenue\nA,10\nA,11\nB,12\nB,13\nB,100\n",
        date_column="",
        categories=["segment"],
    )

    report = generate_insights(path, profile=profile, configuration=configuration)
    anomaly = _one(report, "iqr_anomaly_detection")

    assert anomaly.observation["q1"] == 11
    assert anomaly.observation["q3"] == 13
    assert anomaly.observation["lower_bound"] == 8
    assert anomaly.observation["upper_bound"] == 16
    assert anomaly.observation["anomaly_count"] == 1
    assert anomaly.observation["anomalies"] == [{"row_number": 6, "value": 100.0}]


def test_missing_values_generate_exact_warning(tmp_path: Path) -> None:
    path, profile, configuration = _configured_dataset(
        tmp_path,
        "segment,revenue\nA,10\nA,NA\nB,20\nB,30\nB,40\n",
        date_column="",
        categories=["segment"],
    )

    report = generate_insights(path, profile=profile, configuration=configuration)
    warning = _one(report, "missing_data_warning")

    assert warning.metric == "revenue"
    assert warning.observation == {
        "missing_count": 1,
        "missing_percentage": 20.0,
        "total_records": 5,
    }


def test_no_date_dataset_skips_all_temporal_analysis(tmp_path: Path) -> None:
    path, profile, configuration = _configured_dataset(
        tmp_path,
        "segment,revenue\nA,10\nA,20\nB,30\nB,40\nB,50\n",
        date_column="",
        categories=["segment"],
    )

    report = generate_insights(path, profile=profile, configuration=configuration)
    types = {insight.type for insight in report.insights}
    skipped = _one(report, "analysis_skipped")

    assert skipped.observation["reason"] == "no_date_column"
    assert "period_change" not in types
    assert "trend" not in types
    assert "segment_contribution" not in types


def test_missing_date_skips_temporal_analysis_instead_of_imputing(tmp_path: Path) -> None:
    path, profile, configuration = _configured_dataset(
        tmp_path,
        (
            "date,segment,revenue\n"
            "2026-01-01,A,10\n"
            "2026-01-02,A,20\n"
            ",B,30\n"
            "2026-02-01,B,40\n"
            "2026-02-02,B,50\n"
        ),
        categories=["segment"],
    )

    report = generate_insights(path, profile=profile, configuration=configuration)
    skipped = [
        insight
        for insight in report.insights
        if insight.type == "analysis_skipped"
        and insight.observation.get("reason") == "missing_dates"
    ]

    assert len(skipped) == 1
    assert skipped[0].observation["missing_date_count"] == 1
    assert not any(insight.type == "period_change" for insight in report.insights)


def test_zero_prior_and_zero_overall_change_are_explicit(tmp_path: Path) -> None:
    path, profile, configuration = _configured_dataset(
        tmp_path,
        (
            "date,segment,revenue\n"
            "2026-01-01,A,10\n"
            "2026-01-02,B,-10\n"
            "2026-02-01,A,20\n"
            "2026-02-02,B,-20\n"
        ),
        categories=["segment"],
    )

    report = generate_insights(path, profile=profile, configuration=configuration)
    period = _one(report, "period_change")
    contribution = _one(report, "segment_contribution")

    assert period.observation["previous_value"] == 0
    assert period.observation["percentage_change"] is None
    assert contribution.observation["overall_change"] == 0
    assert contribution.observation["percentage_status"] == (
        "not_calculated_zero_overall_change"
    )
    assert contribution.observation["reconciled_percentage_total"] is None
    assert all(
        item["contribution_percentage"] is None
        for item in contribution.observation["contributions"]  # type: ignore[index]
    )


def test_nonterminating_contribution_percentages_reconcile_exactly(
    tmp_path: Path,
) -> None:
    path, profile, configuration = _configured_dataset(
        tmp_path,
        (
            "date,segment,revenue\n"
            "2026-01-01,A,10\n"
            "2026-01-02,B,20\n"
            "2026-01-03,C,30\n"
            "2026-02-01,A,11\n"
            "2026-02-02,B,22\n"
            "2026-02-03,C,34\n"
        ),
        categories=["segment"],
    )

    report = generate_insights(path, profile=profile, configuration=configuration)
    contribution = _one(report, "segment_contribution").observation
    percentages = [
        float(item["contribution_percentage"])
        for item in contribution["contributions"]  # type: ignore[index]
    ]

    assert contribution["overall_change"] == 7
    assert contribution["reconciled_percentage_total"] == 100
    assert math.fsum(percentages) == 100


def test_small_samples_warn_and_skip_unsupported_calculations(tmp_path: Path) -> None:
    path, profile, configuration = _configured_dataset(
        tmp_path,
        "segment,revenue\nA,10\nB,20\nB,30\n",
        date_column="",
    )

    report = generate_insights(path, profile=profile, configuration=configuration)
    warnings = [
        insight for insight in report.insights if insight.type == "insufficient_data_warning"
    ]

    assert any(item.observation.get("reason") == "small_dataset" for item in warnings)
    assert any(item.observation.get("analysis") == "iqr_anomaly_detection" for item in warnings)
    assert not any(item.type == "iqr_anomaly_detection" for item in report.insights)


def test_every_insight_has_valid_shape_and_existing_source_columns(tmp_path: Path) -> None:
    path, profile, configuration = _main_dataset(tmp_path)

    report = generate_insights(path, profile=profile, configuration=configuration)
    existing = {column.name for column in profile.columns}
    payload = report.to_dict()

    assert [item.id for item in report.insights] == [
        f"INS-{index:03d}" for index in range(1, len(report.insights) + 1)
    ]
    for insight in report.insights:
        assert set(insight.to_dict()) == {
            "id",
            "type",
            "metric",
            "observation",
            "source_columns",
            "filters",
            "record_count",
            "confidence",
            "limitations",
        }
        assert insight.metric in existing
        assert set(insight.source_columns).issubset(existing)
        assert insight.confidence in {"high", "medium", "low"}
        assert insight.record_count >= 0
    json.dumps(payload)


def test_report_is_reproducible_and_saved_as_json(tmp_path: Path) -> None:
    path, profile, configuration = _main_dataset(tmp_path)

    first = generate_insights(path, profile=profile, configuration=configuration)
    second = generate_insights(path, profile=profile, configuration=configuration)
    saved_path = save_insight_report(first, insight_dir=tmp_path / "insights")

    assert first.to_dict() == second.to_dict()
    assert json.loads(saved_path.read_text(encoding="utf-8")) == first.to_dict()


def _derived_dataset(
    tmp_path: Path, *, aggregation: str, operation: str, name: str
) -> tuple[Path, DatasetProfile, BusinessConfiguration]:
    path = tmp_path / "derived.csv"
    path.write_text(
        (
            "date,segment,revenue,cost\n"
            "2026-01-01,A,100,0\n"
            "2026-01-02,B,300,300\n"
            "2026-02-01,A,200,100\n"
            "2026-02-02,B,200,100\n"
            "2026-03-01,A,400,100\n"
            "2026-03-02,B,100,100\n"
        ),
        encoding="utf-8",
    )
    profile = profile_csv(path)
    display_format = "percentage" if "percentage" in operation else "currency"
    metric = validate_derived_metric(
        profile,
        name=name,
        operation=operation,
        left_column="revenue",
        right_column="cost",
        aggregation=aggregation,
        display_format=display_format,
    )
    configuration = validate_derived_business_configuration(
        profile,
        dataset_id="d" * 32,
        derived_metric=metric,
        kpi_direction="higher",
        date_column="date",
        category_columns=["segment"],
        target_or_benchmark="",
        business_objective="Evaluate a confirmed derived KPI.",
    )
    return path, profile, configuration


def test_ratio_of_sums_uses_aggregate_inputs_not_average_row_ratios(
    tmp_path: Path,
) -> None:
    path, profile, configuration = _derived_dataset(
        tmp_path,
        aggregation="ratio_of_sums",
        operation="margin_percentage",
        name="Profit margin percent",
    )

    report = generate_insights(path, profile=profile, configuration=configuration)
    period = _one(report, "period_change")
    contribution_skips = [
        insight
        for insight in report.insights
        if insight.type == "analysis_skipped"
        and insight.observation.get("analysis") == "segment_contribution"
    ]

    # February: (400 - 200) / 400 = 50%; March: (500 - 200) / 500 = 60%.
    assert period.observation["previous_value"] == 50
    assert period.observation["current_value"] == 60
    assert period.observation["percentage_change"] == 20
    assert period.observation["aggregation"] == "ratio_of_sums"
    assert len(contribution_skips) == 1
    assert report.metric_definition["metric_type"] == "derived"
    assert report.metric_definition["formula"] == "((revenue - cost) / revenue) × 100"
    assert report.metric_definition["source_columns"] == ["revenue", "cost"]
    assert all(
        "Profit margin percent" not in insight.source_columns
        for insight in report.insights
    )


def test_additive_derived_profit_supports_reconciled_contributions(
    tmp_path: Path,
) -> None:
    path, profile, configuration = _derived_dataset(
        tmp_path,
        aggregation="sum",
        operation="subtract",
        name="Profit",
    )

    report = generate_insights(path, profile=profile, configuration=configuration)
    period = _one(report, "period_change")
    contribution = _one(report, "segment_contribution")

    assert period.observation["previous_value"] == 200
    assert period.observation["current_value"] == 300
    assert period.observation["percentage_change"] == 50
    assert contribution.observation["overall_change"] == 100
    assert contribution.observation["reconciled_percentage_total"] == 100
    assert set(contribution.source_columns) == {"date", "segment", "revenue", "cost"}
