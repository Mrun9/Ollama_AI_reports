"""Validation tests for user-confirmed business configuration."""

from pathlib import Path

import pytest

from insight_reporter.business_config import (
    BusinessConfigurationError,
    validate_business_configuration,
)
from insight_reporter.dataset_profile import DatasetProfile, profile_csv


def _profile(tmp_path: Path) -> DatasetProfile:
    path = tmp_path / "business.csv"
    path.write_text(
        (
            "customer_id,date,region,revenue\n"
            "C-1,2026-01-01,North,100\n"
            "C-2,2026-01-02,South,120\n"
            "C-3,2026-01-03,North,140\n"
        ),
        encoding="utf-8",
    )
    return profile_csv(path)


def _valid_arguments(profile: DatasetProfile) -> dict[str, object]:
    return {
        "profile": profile,
        "dataset_id": "a" * 32,
        "primary_kpi": "revenue",
        "kpi_direction": "higher",
        "date_column": "date",
        "category_columns": ["region"],
        "target_or_benchmark": "150",
        "business_objective": "Increase regional revenue.",
    }


def test_valid_user_selections_create_configuration(tmp_path: Path) -> None:
    profile = _profile(tmp_path)

    configuration = validate_business_configuration(**_valid_arguments(profile))  # type: ignore[arg-type]

    assert configuration.primary_kpi == "revenue"
    assert configuration.kpi_direction == "higher"
    assert configuration.date_column == "date"
    assert configuration.category_columns == ("region",)
    assert configuration.target_or_benchmark == 150
    assert configuration.business_objective == "Increase regional revenue."
    assert configuration.source_sha256 == profile.source_sha256


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("dataset_id", "../../escape", "Dataset ID is invalid"),
        ("primary_kpi", "missing_column", "measurable KPI"),
        ("primary_kpi", "customer_id", "measurable KPI"),
        ("kpi_direction", "sideways", "higher or lower"),
        ("date_column", "region", "valid date candidate"),
        ("category_columns", ["revenue"], "category candidates"),
        ("target_or_benchmark", "nan", "finite number"),
        ("business_objective", "   ", "objective is required"),
    ],
)
def test_invalid_user_selections_are_rejected(
    tmp_path: Path, field: str, invalid_value: object, message: str
) -> None:
    arguments = _valid_arguments(_profile(tmp_path))
    arguments[field] = invalid_value

    with pytest.raises(BusinessConfigurationError, match=message):
        validate_business_configuration(**arguments)  # type: ignore[arg-type]


def test_no_date_selection_is_valid_when_dataset_has_no_date(tmp_path: Path) -> None:
    path = tmp_path / "no-date.csv"
    path.write_text("segment,score\nEast,10\nWest,20\nEast,30\n", encoding="utf-8")
    profile = profile_csv(path)

    configuration = validate_business_configuration(
        profile,
        dataset_id="b" * 32,
        primary_kpi="score",
        kpi_direction="higher",
        date_column="",
        category_columns=["segment"],
        target_or_benchmark="",
        business_objective="Track score by segment.",
    )

    assert configuration.date_column is None
    assert configuration.target_or_benchmark is None
