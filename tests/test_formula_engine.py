"""Safety and correctness tests for multi-variable derived KPI formulas."""

from pathlib import Path

import pytest

from insight_reporter.dataset_profile import profile_csv
from insight_reporter.dataset_view import source_id_from_hash
from insight_reporter.derived_metrics import (
    DerivedMetricError,
    aggregate_derived_metric,
    evaluate_derived_metric,
    validate_formula_metric,
)


def _profile(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "formula.csv"
    path.write_text(
        "revenue,cost,discount,tax\n"
        "100,60,5,2\n"
        "200,120,10,4\n",
        encoding="utf-8",
    )
    return profile_csv(path)


def test_row_formula_supports_more_than_two_columns(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    metric = validate_formula_metric(
        profile,
        name="Adjusted profit",
        formula="[revenue] - [cost] - [discount] - [tax]",
        calculation_level="row",
        aggregation="sum",
        display_format="currency",
        source_id=source_id_from_hash(profile.source_sha256),
    )

    result = evaluate_derived_metric(
        metric,
        {"revenue": "100", "cost": "60", "discount": "5", "tax": "2"},
    )

    assert metric.source_columns == ("revenue", "cost", "discount", "tax")
    assert result.value == 33
    assert result.status == "valid"


def test_aggregate_formula_uses_aggregated_source_columns(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    metric = validate_formula_metric(
        profile,
        name="Margin",
        formula="(SUM([revenue]) - SUM([cost])) / SUM([revenue]) * 100",
        calculation_level="aggregate",
        aggregation="formula",
        display_format="percentage",
        source_id=source_id_from_hash(profile.source_sha256),
    )

    result = aggregate_derived_metric(
        metric,
        (
            {"revenue": "100", "cost": "60"},
            {"revenue": "200", "cost": "120"},
        ),
    )

    assert result.value == 40
    assert result.status == "valid"


@pytest.mark.parametrize(
    "formula",
    [
        "__import__('os')",
        "[revenue] ** 2",
        "open([revenue])",
        "[missing] + 1",
    ],
)
def test_unsafe_or_unknown_formula_syntax_is_rejected(
    tmp_path: Path, formula: str
) -> None:
    profile = _profile(tmp_path)

    with pytest.raises(DerivedMetricError):
        validate_formula_metric(
            profile,
            name="Unsafe",
            formula=formula,
            calculation_level="row",
            aggregation="sum",
            display_format="number",
            source_id=source_id_from_hash(profile.source_sha256),
        )


def test_aggregate_formula_requires_all_columns_inside_functions(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path)

    with pytest.raises(DerivedMetricError, match="every column"):
        validate_formula_metric(
            profile,
            name="Mixed scope",
            formula="SUM([revenue]) - [cost]",
            calculation_level="aggregate",
            aggregation="formula",
            display_format="number",
            source_id=source_id_from_hash(profile.source_sha256),
        )


def test_formula_nesting_limit_fails_cleanly(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    formula = ("(" * 30) + "[revenue]" + (")" * 30)

    with pytest.raises(DerivedMetricError, match="nesting is too deep"):
        validate_formula_metric(
            profile,
            name="Too deep",
            formula=formula,
            calculation_level="row",
            aggregation="sum",
            display_format="number",
            source_id=source_id_from_hash(profile.source_sha256),
        )
