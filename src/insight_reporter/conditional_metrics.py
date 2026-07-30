"""Validated conditional percentage KPIs over one retained dataset."""

from __future__ import annotations

import math
from dataclasses import dataclass

from insight_reporter.dataset_profile import (
    ColumnType,
    DatasetProfile,
)
from insight_reporter.dataset_view import ColumnReference, DatasetView

CALCULATION_BASES = ("record_count", "value_sum")
_MAX_NAME_CHARACTERS = 120
_MAX_INCLUDED_VALUES = 20
_MAX_VALUE_CHARACTERS = 200
_MISSING_MARKERS = frozenset({"", "na", "n/a", "null", "none", "nan"})


class ConditionalMetricError(ValueError):
    """Raised when a conditional-rate definition is unsafe or inconsistent."""


@dataclass(frozen=True)
class ConditionalMetricEvaluation:
    numerator: float
    denominator: float
    percentage: float | None
    numerator_record_count: int
    denominator_record_count: int


@dataclass(frozen=True)
class ConditionalMetric:
    schema_version: int
    name: str
    calculation_base: str
    condition_reference: ColumnReference
    included_values: tuple[str, ...]
    value_reference: ColumnReference | None
    row_grain_confirmed: bool

    @property
    def condition_column(self) -> str:
        return self.condition_reference.column

    @property
    def value_column(self) -> str | None:
        return (
            self.value_reference.column
            if self.value_reference is not None
            else None
        )

    @property
    def source_columns(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *((self.value_column,) if self.value_column else ()),
                    self.condition_column,
                )
            )
        )

    @property
    def formula_label(self) -> str:
        values = ", ".join(f'"{value}"' for value in self.included_values)
        if self.calculation_base == "record_count":
            return (
                f"COUNT(rows where [{self.condition_column}] IN ({values})) "
                "/ COUNT(all rows) × 100"
            )
        return (
            f"SUM([{self.value_column}] where [{self.condition_column}] "
            f"IN ({values})) / SUM([{self.value_column}]) × 100"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "name": self.name,
            "calculation_base": self.calculation_base,
            "condition_reference": self.condition_reference.to_dict(),
            "included_values": list(self.included_values),
            "value_reference": (
                self.value_reference.to_dict()
                if self.value_reference is not None
                else None
            ),
            "row_grain_confirmed": self.row_grain_confirmed,
            "zero_denominator": "return_null",
            "missing_condition": "include_in_denominator",
        }


def condition_value_options(
    view: DatasetView,
    profile: DatasetProfile,
    *,
    maximum_per_column: int = 100,
) -> dict[str, tuple[str, ...]]:
    """Return exact, bounded values for safe condition checkboxes."""

    candidates = _condition_candidates(profile)
    values: dict[str, set[str]] = {column: set() for column in candidates}
    for row in view.iter_rows():
        for column in candidates:
            value = row.values[column].strip()
            if value and value.casefold() not in _MISSING_MARKERS:
                values[column].add(value)
    return {
        column: tuple(
            sorted(column_values, key=str.casefold)[:maximum_per_column]
        )
        for column, column_values in values.items()
        if column_values
    }


def validate_conditional_metric(
    profile: DatasetProfile,
    view: DatasetView,
    *,
    name: str,
    calculation_base: str,
    condition_column: str,
    included_values: list[str],
    value_column: str,
    row_grain_confirmed: bool,
    source_id: str,
) -> ConditionalMetric:
    """Validate a new conditional rate against exact retained values."""

    metric = _build_conditional_metric(
        profile,
        name=name,
        calculation_base=calculation_base,
        condition_column=condition_column,
        included_values=included_values,
        value_column=value_column,
        row_grain_confirmed=row_grain_confirmed,
        source_id=source_id,
    )
    options = condition_value_options(view, profile)
    available_values = set(options.get(metric.condition_column, ()))
    if not set(metric.included_values).issubset(available_values):
        raise ConditionalMetricError(
            "Selected condition values do not exist in the retained condition column."
        )
    return metric


def load_conditional_metric(
    profile: DatasetProfile,
    payload: object,
    *,
    source_id: str,
) -> ConditionalMetric:
    """Load and structurally revalidate a persisted conditional rate."""

    expected = {
        "schema_version",
        "name",
        "calculation_base",
        "condition_reference",
        "included_values",
        "value_reference",
        "row_grain_confirmed",
        "zero_denominator",
        "missing_condition",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected
        or payload.get("schema_version") != 1
        or payload.get("zero_denominator") != "return_null"
        or payload.get("missing_condition") != "include_in_denominator"
    ):
        raise ConditionalMetricError(
            "Saved conditional KPI has an invalid shape."
        )
    condition = payload.get("condition_reference")
    value = payload.get("value_reference")
    if (
        not isinstance(condition, dict)
        or condition.get("source_id") != source_id
        or not isinstance(condition.get("column"), str)
    ):
        raise ConditionalMetricError(
            "Saved conditional KPI condition reference is invalid."
        )
    if value is not None and (
        not isinstance(value, dict)
        or value.get("source_id") != source_id
        or not isinstance(value.get("column"), str)
    ):
        raise ConditionalMetricError(
            "Saved conditional KPI value reference is invalid."
        )
    included_values = payload.get("included_values")
    row_grain_confirmed = payload.get("row_grain_confirmed")
    if (
        not isinstance(payload.get("name"), str)
        or not isinstance(payload.get("calculation_base"), str)
        or not isinstance(included_values, list)
        or not all(isinstance(item, str) for item in included_values)
        or not isinstance(row_grain_confirmed, bool)
    ):
        raise ConditionalMetricError(
            "Saved conditional KPI contains invalid fields."
        )
    return _build_conditional_metric(
        profile,
        name=payload["name"],
        calculation_base=payload["calculation_base"],
        condition_column=condition["column"],
        included_values=included_values,
        value_column=value["column"] if isinstance(value, dict) else "",
        row_grain_confirmed=row_grain_confirmed,
        source_id=source_id,
    )


def evaluate_conditional_metric(
    metric: ConditionalMetric,
    rows: tuple[dict[str, object], ...],
) -> ConditionalMetricEvaluation:
    """Calculate a conditional count or value share in percentage units."""

    included = set(metric.included_values)
    numerator = 0.0
    denominator = 0.0
    numerator_records = 0
    denominator_records = 0
    for row in rows:
        condition_value = str(row.get(metric.condition_column, "")).strip()
        matches = condition_value in included
        if metric.calculation_base == "record_count":
            denominator += 1.0
            denominator_records += 1
            if matches:
                numerator += 1.0
                numerator_records += 1
            continue
        number = _number(row.get(metric.value_column or ""))
        if number is None:
            continue
        denominator += number
        denominator_records += 1
        if matches:
            numerator += number
            numerator_records += 1
    percentage = (
        None
        if math.isclose(denominator, 0.0, abs_tol=1e-12)
        else (numerator / denominator) * 100
    )
    if percentage is not None and not math.isfinite(percentage):
        percentage = None
    return ConditionalMetricEvaluation(
        numerator=_clean(numerator),
        denominator=_clean(denominator),
        percentage=_clean(percentage) if percentage is not None else None,
        numerator_record_count=numerator_records,
        denominator_record_count=denominator_records,
    )


def _build_conditional_metric(
    profile: DatasetProfile,
    *,
    name: str,
    calculation_base: str,
    condition_column: str,
    included_values: list[str],
    value_column: str,
    row_grain_confirmed: bool,
    source_id: str,
) -> ConditionalMetric:
    clean_name = " ".join(name.split())
    if not clean_name or len(clean_name) > _MAX_NAME_CHARACTERS:
        raise ConditionalMetricError(
            f"Conditional KPI name must contain 1 to {_MAX_NAME_CHARACTERS} characters."
        )
    if profile.column(clean_name) is not None:
        raise ConditionalMetricError(
            "Conditional KPI name must not duplicate a source column."
        )
    if calculation_base not in CALCULATION_BASES:
        raise ConditionalMetricError(
            "Conditional KPI calculation base is invalid."
        )
    if condition_column not in _condition_candidates(profile):
        raise ConditionalMetricError(
            "Select a categorical or boolean condition column."
        )
    # Category values are data, not labels authored by the user. Preserve their
    # internal characters exactly so a value such as "New  Customer" continues
    # to match the retained source row; only CSV-adjacent outer whitespace is
    # ignored consistently by the option builder and evaluator.
    clean_values = tuple(
        dict.fromkeys(value.strip() for value in included_values)
    )
    if (
        not clean_values
        or len(clean_values) > _MAX_INCLUDED_VALUES
        or any(
            not value or len(value) > _MAX_VALUE_CHARACTERS
            for value in clean_values
        )
    ):
        raise ConditionalMetricError(
            f"Select between 1 and {_MAX_INCLUDED_VALUES} condition values."
        )
    selected_value_column: str | None = None
    if calculation_base == "value_sum":
        if value_column not in profile.kpi_candidates:
            raise ConditionalMetricError(
                "Value-share KPIs require a measurable numeric value column."
            )
        selected_value_column = value_column
    elif not row_grain_confirmed:
        raise ConditionalMetricError(
            "Confirm that one dataset row represents one denominator event."
        )
    return ConditionalMetric(
        schema_version=1,
        name=clean_name,
        calculation_base=calculation_base,
        condition_reference=ColumnReference(source_id, condition_column),
        included_values=clean_values,
        value_reference=(
            ColumnReference(source_id, selected_value_column)
            if selected_value_column is not None
            else None
        ),
        row_grain_confirmed=row_grain_confirmed,
    )


def _condition_candidates(profile: DatasetProfile) -> tuple[str, ...]:
    return tuple(
        column.name
        for column in profile.columns
        if column.inferred_type
        in {ColumnType.CATEGORICAL, ColumnType.BOOLEAN}
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text or text.casefold() in _MISSING_MARKERS:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _clean(value: float) -> float:
    if math.isclose(value, 0.0, abs_tol=1e-12):
        return 0.0
    return float(f"{value:.12g}")
