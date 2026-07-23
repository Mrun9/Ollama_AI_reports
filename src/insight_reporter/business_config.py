"""Validated business selections derived from an actual dataset profile."""

import json
import math
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from insight_reporter.dataset_profile import DatasetProfile
from insight_reporter.derived_metrics import (
    DerivedMetric,
    DerivedMetricError,
    load_derived_metric,
    validate_derived_metric,
)

_DIRECTIONS = frozenset({"higher", "lower"})
_MAX_OBJECTIVE_CHARACTERS = 2_000


class BusinessConfigurationError(ValueError):
    """Raised when a user selection does not match the retained dataset."""


_V1_CONFIGURATION_KEYS = frozenset(
    {
        "schema_version",
        "dataset_id",
        "source_sha256",
        "primary_kpi",
        "kpi_direction",
        "date_column",
        "category_columns",
        "target_or_benchmark",
        "business_objective",
    }
)
_V2_CONFIGURATION_KEYS = _V1_CONFIGURATION_KEYS | {"metric_type", "derived_metric"}


@dataclass(frozen=True)
class BusinessConfiguration:
    schema_version: int
    dataset_id: str
    source_sha256: str
    primary_kpi: str
    kpi_direction: str
    date_column: str | None
    category_columns: tuple[str, ...]
    target_or_benchmark: float | None
    business_objective: str
    metric_type: str = "source"
    derived_metric: DerivedMetric | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "source_sha256": self.source_sha256,
            "primary_kpi": self.primary_kpi,
            "kpi_direction": self.kpi_direction,
            "date_column": self.date_column,
            "category_columns": list(self.category_columns),
            "target_or_benchmark": self.target_or_benchmark,
            "business_objective": self.business_objective,
        }
        if self.schema_version >= 2:
            payload["metric_type"] = self.metric_type
            payload["derived_metric"] = (
                self.derived_metric.to_dict() if self.derived_metric is not None else None
            )
        return payload


def validate_business_configuration(
    profile: DatasetProfile,
    *,
    dataset_id: str,
    primary_kpi: str,
    kpi_direction: str,
    date_column: str,
    category_columns: list[str],
    target_or_benchmark: str,
    business_objective: str,
) -> BusinessConfiguration:
    """Create a configuration only from candidates present in the profile."""

    if primary_kpi not in profile.kpi_candidates:
        raise BusinessConfigurationError("Select a measurable KPI from the available candidates.")

    return _build_configuration(
        profile,
        schema_version=2,
        dataset_id=dataset_id,
        primary_kpi=primary_kpi,
        metric_type="source",
        derived_metric=None,
        kpi_direction=kpi_direction,
        date_column=date_column,
        category_columns=category_columns,
        target_or_benchmark=target_or_benchmark,
        business_objective=business_objective,
    )


def validate_derived_business_configuration(
    profile: DatasetProfile,
    *,
    dataset_id: str,
    derived_metric: DerivedMetric,
    kpi_direction: str,
    date_column: str,
    category_columns: list[str],
    target_or_benchmark: str,
    business_objective: str,
) -> BusinessConfiguration:
    """Create a configuration from a revalidated restricted derived KPI."""

    try:
        safe_metric = validate_derived_metric(
            profile,
            name=derived_metric.name,
            operation=derived_metric.operation,
            left_column=derived_metric.left_column,
            right_column=derived_metric.right_column,
            aggregation=derived_metric.aggregation,
            display_format=derived_metric.display_format,
        )
    except DerivedMetricError as error:
        raise BusinessConfigurationError(str(error)) from error

    return _build_configuration(
        profile,
        schema_version=2,
        dataset_id=dataset_id,
        primary_kpi=safe_metric.name,
        metric_type="derived",
        derived_metric=safe_metric,
        kpi_direction=kpi_direction,
        date_column=date_column,
        category_columns=category_columns,
        target_or_benchmark=target_or_benchmark,
        business_objective=business_objective,
    )


def _build_configuration(
    profile: DatasetProfile,
    *,
    schema_version: int,
    dataset_id: str,
    primary_kpi: str,
    metric_type: str,
    derived_metric: DerivedMetric | None,
    kpi_direction: str,
    date_column: str,
    category_columns: list[str],
    target_or_benchmark: str,
    business_objective: str,
) -> BusinessConfiguration:
    if re.fullmatch(r"[0-9a-f]{32}", dataset_id) is None:
        raise BusinessConfigurationError("Dataset ID is invalid.")

    if kpi_direction not in _DIRECTIONS:
        raise BusinessConfigurationError("KPI direction must be either higher or lower.")

    selected_date = date_column.strip() or None
    if selected_date is not None and selected_date not in profile.date_candidates:
        raise BusinessConfigurationError("Selected date column is not a valid date candidate.")

    if len(category_columns) != len(set(category_columns)):
        raise BusinessConfigurationError("Category selections must not contain duplicates.")
    invalid_categories = [
        column for column in category_columns if column not in profile.category_candidates
    ]
    if invalid_categories:
        raise BusinessConfigurationError(
            "Category selections must come from the available category candidates."
        )

    target = _parse_optional_target(target_or_benchmark)
    objective = business_objective.strip()
    if not objective:
        raise BusinessConfigurationError("Business objective is required.")
    if len(objective) > _MAX_OBJECTIVE_CHARACTERS:
        raise BusinessConfigurationError(
            f"Business objective must be at most {_MAX_OBJECTIVE_CHARACTERS} characters."
        )

    return BusinessConfiguration(
        schema_version=schema_version,
        dataset_id=dataset_id,
        source_sha256=profile.source_sha256,
        primary_kpi=primary_kpi,
        kpi_direction=kpi_direction,
        date_column=selected_date,
        category_columns=tuple(category_columns),
        target_or_benchmark=target,
        business_objective=objective,
        metric_type=metric_type,
        derived_metric=derived_metric,
    )


def save_business_configuration(
    configuration: BusinessConfiguration, *, configuration_dir: Path
) -> Path:
    """Atomically persist non-sensitive selections outside the static tree."""

    configuration_dir.mkdir(parents=True, exist_ok=True)
    final_path = configuration_dir / f"{configuration.dataset_id}.json"
    temporary_path = configuration_dir / (
        f".{configuration.dataset_id}.{secrets.token_hex(8)}.part"
    )
    payload = json.dumps(
        configuration.to_dict(),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )

    try:
        temporary_path.write_text(f"{payload}\n", encoding="utf-8")
        temporary_path.replace(final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return final_path


def load_business_configuration(
    path: Path, *, profile: DatasetProfile
) -> BusinessConfiguration:
    """Load and revalidate a retained configuration against its source profile."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BusinessConfigurationError("Saved business configuration is unreadable.") from error

    if not isinstance(payload, dict):
        raise BusinessConfigurationError("Saved business configuration has an invalid shape.")
    schema_version = payload.get("schema_version")
    expected_keys = (
        _V1_CONFIGURATION_KEYS if schema_version == 1 else _V2_CONFIGURATION_KEYS
    )
    if set(payload) != expected_keys:
        raise BusinessConfigurationError("Saved business configuration has an invalid shape.")
    if schema_version not in {1, 2}:
        raise BusinessConfigurationError("Saved business configuration version is unsupported.")
    if payload.get("source_sha256") != profile.source_sha256:
        raise BusinessConfigurationError(
            "Saved business configuration does not match the retained dataset."
        )

    dataset_id = payload.get("dataset_id")
    primary_kpi = payload.get("primary_kpi")
    kpi_direction = payload.get("kpi_direction")
    date_column = payload.get("date_column")
    category_columns = payload.get("category_columns")
    target = payload.get("target_or_benchmark")
    objective = payload.get("business_objective")
    if not all(
        isinstance(value, str)
        for value in (dataset_id, primary_kpi, kpi_direction, objective)
    ):
        raise BusinessConfigurationError("Saved business configuration contains invalid text.")
    if date_column is not None and not isinstance(date_column, str):
        raise BusinessConfigurationError("Saved business configuration contains an invalid date.")
    if not isinstance(category_columns, list) or not all(
        isinstance(column, str) for column in category_columns
    ):
        raise BusinessConfigurationError(
            "Saved business configuration contains invalid categories."
        )
    if isinstance(target, bool) or (
        target is not None and not isinstance(target, int | float)
    ):
        raise BusinessConfigurationError("Saved business configuration contains an invalid target.")

    common = {
        "profile": profile,
        "dataset_id": dataset_id,
        "kpi_direction": kpi_direction,
        "date_column": date_column or "",
        "category_columns": category_columns,
        "target_or_benchmark": "" if target is None else str(target),
        "business_objective": objective,
    }
    if schema_version == 1:
        if primary_kpi not in profile.kpi_candidates:
            raise BusinessConfigurationError(
                "Select a measurable KPI from the available candidates."
            )
        return _build_configuration(
            **common,
            schema_version=1,
            primary_kpi=primary_kpi,
            metric_type="source",
            derived_metric=None,
        )

    metric_type = payload.get("metric_type")
    derived_payload = payload.get("derived_metric")
    if metric_type == "source" and derived_payload is None:
        return validate_business_configuration(
            **common,
            primary_kpi=primary_kpi,
        )
    if metric_type != "derived":
        raise BusinessConfigurationError("Saved business configuration metric type is invalid.")
    try:
        derived_metric = load_derived_metric(profile, derived_payload)
    except DerivedMetricError as error:
        raise BusinessConfigurationError(str(error)) from error
    if primary_kpi != derived_metric.name:
        raise BusinessConfigurationError("Saved derived KPI name does not match the primary KPI.")
    return validate_derived_business_configuration(
        **common,
        derived_metric=derived_metric,
    )


def _parse_optional_target(value: str) -> float | None:
    candidate = value.strip()
    if not candidate:
        return None
    try:
        target = float(candidate)
    except ValueError as error:
        raise BusinessConfigurationError("Target or benchmark must be a number.") from error
    if not math.isfinite(target):
        raise BusinessConfigurationError("Target or benchmark must be a finite number.")
    return target
