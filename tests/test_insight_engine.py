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
    target_scope: str = "row",
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
        target_scope=target_scope,
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
    baseline = _one(report, "period_baseline_comparison")
    trend = _one(report, "trend")

    assert period.observation["previous_period"] == "2026-02"
    assert period.observation["previous_value"] == 60
    assert period.observation["current_period"] == "2026-03"
    assert period.observation["current_value"] == 100
    assert period.observation["absolute_change"] == 40
    assert period.observation["percentage_change"] == pytest.approx(200 / 3)
    assert period.observation["direction"] == "increasing"
    assert period.observation["favorable"] is True
    assert baseline.observation["baseline_periods"] == [
        "2026-01",
        "2026-02",
    ]
    assert baseline.observation["baseline_value"] == 45
    assert baseline.observation["current_period"] == "2026-03"
    assert baseline.observation["current_value"] == 100
    assert baseline.observation["absolute_change"] == 55
    assert baseline.observation["percentage_change"] == pytest.approx(
        1100 / 9
    )
    assert trend.observation["slope_per_period"] == 35
    assert trend.observation["direction"] == "increasing"


def test_complete_dataset_target_compares_only_the_dataset_aggregate(
    tmp_path: Path,
) -> None:
    path, profile, configuration = _configured_dataset(
        tmp_path,
        (
            "date,segment,revenue\n"
            "2026-01-01,A,40\n"
            "2026-01-02,B,50\n"
            "2026-02-01,A,60\n"
            "2026-02-02,B,70\n"
            "2026-03-01,A,80\n"
            "2026-03-02,B,90\n"
        ),
        categories=["segment"],
        target="350",
        target_scope="dataset",
    )

    report = generate_insights(path, profile=profile, configuration=configuration)
    snapshot = _one(report, "metric_snapshot")

    assert snapshot.observation["current_value"] == 390
    assert snapshot.observation["target_scope"] == "dataset"
    assert snapshot.observation["gap_to_target"] == 40
    assert snapshot.observation["meets_target"] is True
    assert not any(
        insight.type in {
            "benchmark_breach",
            "period_target_comparison",
            "segment_target_comparison",
        }
        for insight in report.insights
    )


def test_period_target_compares_each_period_aggregate(tmp_path: Path) -> None:
    path, profile, configuration = _main_dataset(tmp_path, target="70")
    configuration = validate_business_configuration(
        profile,
        dataset_id=configuration.dataset_id,
        primary_kpi="revenue",
        kpi_direction="higher",
        date_column="date",
        category_columns=["segment"],
        target_or_benchmark="70",
        target_scope="period",
        business_objective="Meet the revenue target every month.",
    )

    report = generate_insights(path, profile=profile, configuration=configuration)
    comparison = _one(report, "period_target_comparison")

    assert comparison.observation["target_scope"] == "period"
    assert comparison.observation["current_period"] == "2026-03"
    assert comparison.observation["current_value"] == 100
    assert comparison.observation["current_gap_to_target"] == 30
    assert comparison.observation["current_meets_target"] is True
    assert comparison.observation["missed_period_count"] == 2
    assert [item["value"] for item in comparison.observation["period_performance"]] == [
        30,
        60,
        100,
    ]
    assert not any(
        insight.type == "benchmark_breach" for insight in report.insights
    )


def test_segment_target_compares_each_segment_aggregate(tmp_path: Path) -> None:
    path, profile, configuration = _main_dataset(tmp_path, target="100")
    configuration = validate_business_configuration(
        profile,
        dataset_id=configuration.dataset_id,
        primary_kpi="revenue",
        kpi_direction="higher",
        date_column="date",
        category_columns=["segment"],
        target_or_benchmark="100",
        target_scope="segment",
        business_objective="Meet the revenue target in every segment.",
    )

    report = generate_insights(path, profile=profile, configuration=configuration)
    comparison = _one(report, "segment_target_comparison")

    assert comparison.observation["target_scope"] == "segment"
    assert comparison.observation["worst_segment"]["segment"] == "B"
    assert comparison.observation["worst_segment"]["value"] == 70
    assert comparison.observation["best_segment"]["segment"] == "A"
    assert comparison.observation["best_segment"]["value"] == 120
    assert comparison.observation["missed_segment_count"] == 1
    assert not any(
        insight.type in {"benchmark_breach", "segment_benchmark_performance"}
        for insight in report.insights
    )
def test_six_months_are_compared_as_management_quarters(
    tmp_path: Path,
) -> None:
    path, profile, configuration = _configured_dataset(
        tmp_path,
        (
            "date,region,revenue\n"
            "2026-01-02,North,10\n"
            "2026-02-02,North,20\n"
            "2026-03-02,North,30\n"
            "2026-04-02,North,40\n"
            "2026-05-02,North,50\n"
            "2026-06-02,North,60\n"
        ),
    )

    report = generate_insights(
        path,
        profile=profile,
        configuration=configuration,
    )
    period = _one(report, "period_change")

    assert period.observation["period_granularity"] == "quarter"
    assert period.observation["previous_period"] == "2026-Q1"
    assert period.observation["previous_value"] == 60
    assert period.observation["current_period"] == "2026-Q2"
    assert period.observation["current_value"] == 150
    assert period.observation["percentage_change"] == 150


def test_segment_ranking_and_contributions_are_exact(tmp_path: Path) -> None:
    path, profile, configuration = _main_dataset(tmp_path)

    report = generate_insights(path, profile=profile, configuration=configuration)
    ranking = _one(report, "segment_ranking").observation
    cohort = _one(report, "cohort_period_comparison").observation
    contribution = _one(report, "segment_contribution").observation

    assert ranking["top_segment"] == {"segment": "A", "value": 120.0, "record_count": 6}
    assert ranking["bottom_segment"] == {"segment": "B", "value": 70.0, "record_count": 6}
    assert cohort["previous_period"] == "2026-02"
    assert cohort["current_period"] == "2026-03"
    assert cohort["best_performing_change"] == {
        "cohort": "B",
        "previous_value": 20,
        "current_value": 40,
        "absolute_change": 20,
        "percentage_change": 100,
        "direction": "increasing",
        "favorable": True,
        "previous_record_count": 2,
        "current_record_count": 2,
    }
    assert cohort["worst_performing_change"]["cohort"] == "A"
    assert cohort["worst_performing_change"]["percentage_change"] == 50
    assert contribution["overall_change"] == 40
    assert contribution["reconciled_percentage_total"] == 100
    by_segment = {
        item["segment"]: item for item in contribution["contributions"]  # type: ignore[index]
    }
    assert by_segment["A"]["absolute_change"] == 20
    assert by_segment["A"]["contribution_percentage"] == 50
    assert by_segment["B"]["absolute_change"] == 20
    assert by_segment["B"]["contribution_percentage"] == 50


def test_segment_target_performance_identifies_management_priority(
    tmp_path: Path,
) -> None:
    path, profile, configuration = _main_dataset(tmp_path)

    report = generate_insights(
        path,
        profile=profile,
        configuration=configuration,
    )
    target_performance = _one(
        report,
        "segment_benchmark_performance",
    ).observation

    assert target_performance["category_column"] == "segment"
    assert target_performance["target"] == 20
    assert target_performance["kpi_direction"] == "higher"
    assert target_performance["worst_segment"] == {
        "segment": "B",
        "target": 20,
        "average_value": pytest.approx(35 / 3),
        "average_gap_to_target": pytest.approx(-25 / 3),
        "breach_count": 5,
        "record_count": 6,
        "breach_percentage": pytest.approx(250 / 3),
    }
    assert target_performance["best_segment"]["segment"] == "A"
    assert target_performance["best_segment"]["breach_percentage"] == pytest.approx(
        100 / 3
    )


def test_cohort_comparison_respects_lower_is_better_direction(
    tmp_path: Path,
) -> None:
    path, profile, configuration = _configured_dataset(
        tmp_path,
        (
            "date,region,cost\n"
            "2026-01-01,North,60\n"
            "2026-01-02,North,60\n"
            "2026-01-01,South,60\n"
            "2026-01-02,South,60\n"
            "2026-02-01,North,50\n"
            "2026-02-02,North,50\n"
            "2026-02-01,South,60\n"
            "2026-02-02,South,60\n"
            "2026-02-01,East,55\n"
            "2026-02-02,East,55\n"
            "2026-02-01,West,50\n"
            "2026-03-01,North,40\n"
            "2026-03-02,North,40\n"
            "2026-03-01,South,70\n"
            "2026-03-02,South,70\n"
            "2026-03-01,West,45\n"
        ),
        primary_kpi="cost",
        direction="lower",
        categories=["region"],
    )

    report = generate_insights(
        path,
        profile=profile,
        configuration=configuration,
    )
    comparison = _one(
        report,
        "cohort_period_comparison",
    ).observation

    assert comparison["best_performing_change"]["cohort"] == "North"
    assert comparison["best_performing_change"]["favorable"] is True
    assert comparison["worst_performing_change"]["cohort"] == "South"
    assert comparison["worst_performing_change"]["favorable"] is False
    assert comparison["cohort_count"] == 2
    assert comparison["excluded_cohort_count"] == 2


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

    assert warning.metric == "Dataset completeness"
    assert warning.observation == {
        "affected_column_count": 1,
        "total_column_count": 2,
        "maximum_missing_percentage": 20.0,
        "columns": [
            {
                "column": "revenue",
                "missing_count": 1,
                "missing_percentage": 20.0,
                "total_records": 5,
            }
        ],
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
    snapshot = _one(report, "metric_snapshot")

    assert {
        item["analysis"]: item["reason"]
        for item in snapshot.observation["not_applicable_analyses"]
    }["temporal_analyses"] == "requires_confirmed_date_column"
    assert "analysis_skipped" not in types
    assert "period_change" not in types
    assert "period_baseline_comparison" not in types
    assert "trend" not in types
    assert "cohort_period_comparison" not in types
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

    assert len(warnings) == 1
    issues = {
        item["analysis"]: item
        for item in warnings[0].observation["issues"]
    }
    assert issues["dataset_size"]["available"] == 3
    assert issues["dataset_size"]["required"] == 5
    assert issues["iqr_anomaly_detection"]["available"] == 3
    assert "recommendation" in issues["iqr_anomaly_detection"]
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
            "metric_id",
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
    snapshot = _one(report, "metric_snapshot")

    # February: (400 - 200) / 400 = 50%; March: (500 - 200) / 500 = 60%.
    assert period.observation["previous_value"] == 50
    assert period.observation["current_value"] == 60
    assert period.observation["percentage_change"] == 20
    assert period.observation["aggregation"] == "formula"
    excluded = {
        item["analysis"]: item["reason"]
        for item in snapshot.observation["not_applicable_analyses"]
    }
    assert excluded["segment_contribution"] == "requires_additive_sum_metric"
    assert excluded["numeric_correlation"] == "requires_row_level_kpi_values"
    assert not any(
        insight.type == "insufficient_data_warning"
        and any(
            issue["analysis"].startswith("correlation:")
            or issue["analysis"] in {
                "iqr_anomaly_detection",
                "benchmark_breach",
            }
            for issue in insight.observation["issues"]
        )
        for insight in report.insights
    )
    assert not any(
        insight.type == "analysis_skipped"
        and insight.observation.get("analysis") == "segment_contribution"
        for insight in report.insights
    )
    assert report.metric_definition["metric_type"] == "derived"
    assert report.metric_definition["formula"] == (
        "(SUM([revenue]) - SUM([cost])) / SUM([revenue]) * 100"
    )
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


def test_each_configured_kpi_receives_independent_insights(tmp_path: Path) -> None:
    path = tmp_path / "multi-kpi.csv"
    path.write_text(
        "date,segment,revenue,cost\n"
        "2026-01-01,A,100,60\n"
        "2026-01-02,B,200,120\n"
        "2026-02-01,A,150,80\n"
        "2026-02-02,B,250,140\n"
        "2026-03-01,A,180,90\n"
        "2026-03-02,B,300,160\n",
        encoding="utf-8",
    )
    profile = profile_csv(path)
    configuration = validate_business_configuration(
        profile,
        dataset_id="f" * 32,
        primary_kpi="revenue",
        secondary_kpis=["cost"],
        kpi_direction="higher",
        date_column="date",
        category_columns=["segment"],
        target_or_benchmark="",
        business_objective="Compare revenue and cost.",
    )

    report = generate_insights(path, profile=profile, configuration=configuration)

    assert len(report.metric_definitions) == 2
    assert {definition["name"] for definition in report.metric_definitions} == {
        "revenue",
        "cost",
    }
    period_changes = [
        insight for insight in report.insights if insight.type == "period_change"
    ]
    assert {insight.metric for insight in period_changes} == {"revenue", "cost"}
    assert len({insight.metric_id for insight in period_changes}) == 2
