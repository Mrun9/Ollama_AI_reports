"""Correctness and safety tests for restricted derived KPI calculations."""

from pathlib import Path

import pytest

from insight_reporter.dataset_profile import profile_csv
from insight_reporter.derived_metrics import (
    DerivedMetricError,
    evaluate_derived_metric,
    preview_derived_metric,
    validate_derived_metric,
)


def _profile(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "financial.csv"
    path.write_text(
        "revenue,cost,units\n100,60,10\n200,0,20\n,50,5\n",
        encoding="utf-8",
    )
    return path, profile_csv(path)


@pytest.mark.parametrize(
    ("operation", "left", "right", "expected"),
    [
        ("add", 100, 60, 160),
        ("subtract", 100, 60, 40),
        ("multiply", 10, 5, 50),
        ("ratio", 100, 20, 5),
        ("percentage_ratio", 20, 100, 20),
        ("percentage_difference", 120, 100, 20),
        ("margin_percentage", 100, 60, 40),
    ],
)
def test_approved_operations_match_manual_values(
    tmp_path: Path,
    operation: str,
    left: float,
    right: float,
    expected: float,
) -> None:
    _, profile = _profile(tmp_path)
    display_format = "percentage" if "percentage" in operation else "number"
    metric = validate_derived_metric(
        profile,
        name=f"Test {operation}",
        operation=operation,
        left_column="revenue",
        right_column="cost",
        aggregation="mean",
        display_format=display_format,
    )

    result = evaluate_derived_metric(
        metric,
        {"revenue": left, "cost": right},
    )

    assert result.value == expected
    assert result.status == "valid"


def test_division_by_zero_and_missing_inputs_return_null(tmp_path: Path) -> None:
    _, profile = _profile(tmp_path)
    metric = validate_derived_metric(
        profile,
        name="Cost per revenue",
        operation="ratio",
        left_column="cost",
        right_column="revenue",
        aggregation="ratio_of_sums",
        display_format="number",
    )

    zero = evaluate_derived_metric(metric, {"cost": 10, "revenue": 0})
    missing = evaluate_derived_metric(metric, {"cost": 10, "revenue": "NA"})

    assert zero.value is None
    assert zero.status == "division_by_zero"
    assert missing.value is None
    assert missing.status == "missing_input"


def test_preview_counts_valid_missing_and_zero_results(tmp_path: Path) -> None:
    path, profile = _profile(tmp_path)
    metric = validate_derived_metric(
        profile,
        name="Cost per revenue",
        operation="ratio",
        left_column="cost",
        right_column="revenue",
        aggregation="ratio_of_sums",
        display_format="number",
    )

    preview = preview_derived_metric(path, metric)

    assert preview.total_records == 3
    assert preview.valid_result_count == 2
    assert preview.missing_input_count == 1
    assert preview.division_by_zero_count == 0
    assert preview.minimum == 0
    assert preview.maximum == 0.6
    assert preview.mean == 0.3


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("name", "revenue", "must not duplicate"),
        ("operation", "__import__('os').system('id')", "not supported"),
        ("left_column", "unknown", "existing numeric"),
        ("right_column", "revenue", "two different"),
        ("aggregation", "python", "not supported"),
        ("display_format", "html", "not supported"),
    ],
)
def test_unsafe_or_invalid_definitions_are_rejected(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    _, profile = _profile(tmp_path)
    arguments = {
        "profile": profile,
        "name": "Profit",
        "operation": "subtract",
        "left_column": "revenue",
        "right_column": "cost",
        "aggregation": "sum",
        "display_format": "currency",
    }
    arguments[field] = value

    with pytest.raises(DerivedMetricError, match=message):
        validate_derived_metric(**arguments)  # type: ignore[arg-type]


def test_ratio_of_sums_is_restricted_to_ratio_operations(tmp_path: Path) -> None:
    _, profile = _profile(tmp_path)

    with pytest.raises(DerivedMetricError, match="requires a ratio"):
        validate_derived_metric(
            profile,
            name="Invalid aggregate",
            operation="subtract",
            left_column="revenue",
            right_column="cost",
            aggregation="ratio_of_sums",
            display_format="number",
        )


def test_plain_ratio_cannot_be_disguised_as_a_percentage(tmp_path: Path) -> None:
    _, profile = _profile(tmp_path)

    with pytest.raises(DerivedMetricError, match="Non-percentage"):
        validate_derived_metric(
            profile,
            name="Revenue per unit",
            operation="ratio",
            left_column="revenue",
            right_column="units",
            aggregation="ratio_of_sums",
            display_format="percentage",
        )


def test_ratio_cannot_use_additive_sum_aggregation(tmp_path: Path) -> None:
    _, profile = _profile(tmp_path)

    with pytest.raises(DerivedMetricError, match="cannot use additive sum"):
        validate_derived_metric(
            profile,
            name="Revenue per unit",
            operation="ratio",
            left_column="revenue",
            right_column="units",
            aggregation="sum",
            display_format="number",
        )
