"""Validation tests for user-confirmed business configuration."""

import json
from pathlib import Path

import pytest

from insight_reporter.business_config import (
    BusinessConfigurationError,
    add_source_metrics,
    load_business_configuration,
    remove_metric,
    save_business_configuration,
    set_primary_metric,
    update_metric_settings,
    validate_business_configuration,
    validate_derived_business_configuration,
)
from insight_reporter.dataset_profile import DatasetProfile, profile_csv
from insight_reporter.dataset_view import source_id_from_hash
from insight_reporter.derived_metrics import (
    validate_derived_metric,
    validate_formula_metric,
)


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
    assert configuration.schema_version == 4
    assert len(configuration.metrics) == 1
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
            '"column": "revenue"', '"column": "profit"'
        ),
        encoding="utf-8",
    )

    with pytest.raises(BusinessConfigurationError, match="not measurable"):
        load_business_configuration(path, profile=profile)


def test_version_one_source_configuration_remains_supported(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    payload = {
        "schema_version": 1,
        "dataset_id": "a" * 32,
        "source_sha256": profile.source_sha256,
        "primary_kpi": "revenue",
        "kpi_direction": "higher",
        "date_column": "date",
        "category_columns": ["region"],
        "target_or_benchmark": 150.0,
        "business_objective": "Increase regional revenue.",
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_business_configuration(path, profile=profile)

    assert loaded.schema_version == 4
    assert loaded.metric_type == "source"
    assert loaded.primary_kpi == "revenue"


def test_version_two_source_configuration_remains_supported(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    payload = {
        "schema_version": 2,
        "dataset_id": "a" * 32,
        "source_sha256": profile.source_sha256,
        "primary_kpi": "revenue",
        "kpi_direction": "higher",
        "date_column": "date",
        "category_columns": ["region"],
        "target_or_benchmark": 150.0,
        "business_objective": "Increase regional revenue.",
        "metric_type": "source",
        "derived_metric": None,
    }
    path = tmp_path / "version-two.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_business_configuration(path, profile=profile)

    assert loaded.schema_version == 4
    assert loaded.metric_type == "source"
    assert loaded.primary_kpi == "revenue"


def test_version_three_registry_configuration_remains_supported(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path)
    configuration = validate_business_configuration(**_valid_arguments(profile))  # type: ignore[arg-type]
    payload = configuration.to_dict()
    payload["schema_version"] = 3
    payload["sources"][0].pop("table_name")  # type: ignore[index,union-attr]
    path = tmp_path / "version-three.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_business_configuration(path, profile=profile)

    assert loaded.schema_version == 4
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
    assert loaded.derived_metric.formula_label == "[revenue] - [cost]"


def test_multiple_metrics_can_be_managed_without_removing_single_kpi_support(
    tmp_path: Path,
) -> None:
    path = tmp_path / "multi.csv"
    path.write_text(
        "date,region,revenue,cost\n"
        "2026-01-01,North,100,60\n"
        "2026-01-02,South,120,70\n"
        "2026-01-03,North,140,80\n",
        encoding="utf-8",
    )
    profile = profile_csv(path)
    configuration = validate_business_configuration(
        profile,
        dataset_id="e" * 32,
        primary_kpi="revenue",
        secondary_kpis=["cost"],
        kpi_direction="higher",
        date_column="date",
        category_columns=["region"],
        target_or_benchmark="150",
        business_objective="Compare revenue and cost.",
    )

    assert [metric.name for metric in configuration.metrics] == ["revenue", "cost"]
    cost = configuration.metrics[1]
    configuration = update_metric_settings(
        configuration,
        cost.metric_id,
        kpi_direction="lower",
        target_or_benchmark="75",
    )
    configuration = set_primary_metric(configuration, cost.metric_id)

    assert configuration.primary_kpi == "cost"
    assert configuration.kpi_direction == "lower"
    assert configuration.target_or_benchmark == 75
    with pytest.raises(BusinessConfigurationError, match="different primary"):
        remove_metric(configuration, cost.metric_id)


def test_derived_metric_can_be_added_without_replacing_existing_primary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "append-derived.csv"
    path.write_text(
        "date,region,revenue,cost\n"
        "2026-01-01,North,100,60\n"
        "2026-01-02,South,120,70\n"
        "2026-01-03,North,140,80\n",
        encoding="utf-8",
    )
    profile = profile_csv(path)
    configuration = validate_business_configuration(
        profile,
        dataset_id="9" * 32,
        primary_kpi="revenue",
        kpi_direction="higher",
        date_column="date",
        category_columns=["region"],
        target_or_benchmark="",
        business_objective="Compare revenue and profit.",
    )
    formula = validate_formula_metric(
        profile,
        name="Profit",
        formula="[revenue] - [cost]",
        calculation_level="row",
        aggregation="sum",
        display_format="currency",
        source_id=source_id_from_hash(profile.source_sha256),
    )

    updated = validate_derived_business_configuration(
        profile,
        dataset_id="9" * 32,
        derived_metric=formula,
        kpi_direction="higher",
        date_column="date",
        category_columns=["region"],
        target_or_benchmark="",
        business_objective="Compare revenue and profit.",
        existing_configuration=configuration,
        metric_role="secondary",
    )

    assert updated.primary_kpi == "revenue"
    assert [metric.name for metric in updated.metrics] == ["revenue", "Profit"]


def test_source_metrics_are_appended_without_reselecting_primary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "append-source.csv"
    path.write_text(
        "date,region,revenue,cost,units\n"
        "2026-01-01,North,100,60,4\n"
        "2026-01-02,South,120,70,5\n"
        "2026-01-03,North,140,80,6\n",
        encoding="utf-8",
    )
    profile = profile_csv(path)
    configuration = validate_business_configuration(
        profile,
        dataset_id="8" * 32,
        primary_kpi="revenue",
        kpi_direction="higher",
        date_column="date",
        category_columns=["region"],
        target_or_benchmark="",
        business_objective="Compare operational KPIs.",
    )
    original_primary = configuration.primary_metric_id

    configuration = add_source_metrics(
        profile,
        dataset_id="8" * 32,
        source_columns=["cost"],
        kpi_direction="lower",
        existing_configuration=configuration,
    )
    configuration = add_source_metrics(
        profile,
        dataset_id="8" * 32,
        source_columns=["units"],
        kpi_direction="higher",
        existing_configuration=configuration,
    )

    assert configuration.primary_metric_id == original_primary
    assert [metric.name for metric in configuration.metrics] == [
        "revenue",
        "cost",
        "units",
    ]
    assert configuration.metrics[1].kpi_direction == "lower"


def test_different_derived_formula_cannot_silently_replace_same_name(
    tmp_path: Path,
) -> None:
    path = tmp_path / "duplicate-derived-name.csv"
    path.write_text(
        "date,region,revenue,cost\n"
        "2026-01-01,North,100,60\n"
        "2026-01-02,South,120,70\n"
        "2026-01-03,North,140,80\n",
        encoding="utf-8",
    )
    profile = profile_csv(path)
    configuration = validate_business_configuration(
        profile,
        dataset_id="7" * 32,
        primary_kpi="revenue",
        kpi_direction="higher",
        date_column="date",
        category_columns=["region"],
        target_or_benchmark="",
        business_objective="Compare revenue and profit.",
    )
    first = validate_formula_metric(
        profile,
        name="Profit",
        formula="[revenue] - [cost]",
        calculation_level="row",
        aggregation="sum",
        display_format="currency",
        source_id=source_id_from_hash(profile.source_sha256),
    )
    configuration = validate_derived_business_configuration(
        profile,
        dataset_id="7" * 32,
        derived_metric=first,
        kpi_direction="higher",
        date_column="date",
        category_columns=["region"],
        target_or_benchmark="",
        business_objective="Compare revenue and profit.",
        existing_configuration=configuration,
        metric_role="secondary",
    )
    conflicting = validate_formula_metric(
        profile,
        name="Profit",
        formula="[revenue] / [cost]",
        calculation_level="row",
        aggregation="mean",
        display_format="number",
        source_id=source_id_from_hash(profile.source_sha256),
    )

    with pytest.raises(BusinessConfigurationError, match="unique name"):
        validate_derived_business_configuration(
            profile,
            dataset_id="7" * 32,
            derived_metric=conflicting,
            kpi_direction="higher",
            date_column="date",
            category_columns=["region"],
            target_or_benchmark="",
            business_objective="Compare revenue and profit.",
            existing_configuration=configuration,
            metric_role="secondary",
        )

    assert [metric.name for metric in configuration.metrics] == [
        "revenue",
        "Profit",
    ]
