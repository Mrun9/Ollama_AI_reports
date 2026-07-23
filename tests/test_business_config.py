"""Validation tests for user-confirmed business configuration."""

import json
from pathlib import Path

import pytest

from insight_reporter.business_config import (
    BusinessConfigurationError,
    load_business_configuration,
    save_business_configuration,
    validate_business_configuration,
    validate_derived_business_configuration,
)
from insight_reporter.dataset_profile import DatasetProfile, profile_csv
from insight_reporter.derived_metrics import validate_derived_metric


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
    assert configuration.schema_version == 2
    assert configuration.metric_type == "source"
    assert configuration.derived_metric is None


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


def test_saved_configuration_is_loaded_and_revalidated(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    configuration = validate_business_configuration(**_valid_arguments(profile))  # type: ignore[arg-type]
    path = save_business_configuration(
        configuration, configuration_dir=tmp_path / "configurations"
    )

    loaded = load_business_configuration(path, profile=profile)

    assert loaded == configuration


def test_tampered_saved_configuration_is_rejected(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    configuration = validate_business_configuration(**_valid_arguments(profile))  # type: ignore[arg-type]
    path = save_business_configuration(
        configuration, configuration_dir=tmp_path / "configurations"
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"primary_kpi": "revenue"', '"primary_kpi": "profit"'
        ),
        encoding="utf-8",
    )

    with pytest.raises(BusinessConfigurationError, match="measurable KPI"):
        load_business_configuration(path, profile=profile)


def test_version_one_source_configuration_remains_supported(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    configuration = validate_business_configuration(**_valid_arguments(profile))  # type: ignore[arg-type]
    payload = configuration.to_dict()
    payload["schema_version"] = 1
    payload.pop("metric_type")
    payload.pop("derived_metric")
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_business_configuration(path, profile=profile)

    assert loaded.schema_version == 1
    assert loaded.metric_type == "source"
    assert loaded.primary_kpi == "revenue"


def test_derived_configuration_is_saved_and_revalidated(tmp_path: Path) -> None:
    data_path = tmp_path / "derived-business.csv"
    data_path.write_text(
        "date,region,revenue,cost\n"
        "2026-01-01,North,100,60\n"
        "2026-01-02,South,120,70\n"
        "2026-01-03,North,140,80\n",
        encoding="utf-8",
    )
    profile = profile_csv(data_path)
    metric = validate_derived_metric(
        profile,
        name="Profit",
        operation="subtract",
        left_column="revenue",
        right_column="cost",
        aggregation="sum",
        display_format="currency",
    )
    configuration = validate_derived_business_configuration(
        profile,
        dataset_id="c" * 32,
        derived_metric=metric,
        kpi_direction="higher",
        date_column="date",
        category_columns=["region"],
        target_or_benchmark="50",
        business_objective="Evaluate profit performance.",
    )
    path = save_business_configuration(
        configuration, configuration_dir=tmp_path / "configurations"
    )

    loaded = load_business_configuration(path, profile=profile)

    assert loaded == configuration
    assert loaded.metric_type == "derived"
    assert loaded.derived_metric is not None
    assert loaded.derived_metric.formula_label == "revenue - cost"
