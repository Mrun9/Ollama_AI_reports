"""Restricted, deterministic derived-KPI definitions and calculations."""

import csv
import io
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from insight_reporter.dataset_profile import ColumnType, DatasetProfile

OPERATIONS = frozenset(
    {
        "add",
        "subtract",
        "multiply",
        "ratio",
        "percentage_ratio",
        "percentage_difference",
        "margin_percentage",
    }
)
AGGREGATIONS = frozenset({"sum", "mean", "ratio_of_sums"})
DISPLAY_FORMATS = frozenset({"number", "percentage", "currency"})
RATIO_OF_SUMS_OPERATIONS = frozenset(
    {"ratio", "percentage_ratio", "percentage_difference", "margin_percentage"}
)
PERCENTAGE_OPERATIONS = frozenset(
    {"percentage_ratio", "percentage_difference", "margin_percentage"}
)
_MISSING_MARKERS = frozenset({"", "na", "n/a", "null", "none", "nan"})


class DerivedMetricError(ValueError):
    """Raised when a derived KPI definition or calculation is unsafe."""


@dataclass(frozen=True)
class DerivedMetric:
    schema_version: int
    name: str
    operation: str
    left_column: str
    right_column: str
    aggregation: str
    display_format: str

    @property
    def source_columns(self) -> tuple[str, str]:
        return (self.left_column, self.right_column)

    @property
    def formula_label(self) -> str:
        left = self.left_column
        right = self.right_column
        formulas = {
            "add": f"{left} + {right}",
            "subtract": f"{left} - {right}",
            "multiply": f"{left} × {right}",
            "ratio": f"{left} / {right}",
            "percentage_ratio": f"({left} / {right}) × 100",
            "percentage_difference": f"(({left} - {right}) / {right}) × 100",
            "margin_percentage": f"(({left} - {right}) / {left}) × 100",
        }
        return formulas[self.operation]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "operation": self.operation,
            "left_column": self.left_column,
            "right_column": self.right_column,
            "aggregation": self.aggregation,
            "display_format": self.display_format,
            "division_by_zero": "return_null",
            "missing_input": "return_null",
        }


@dataclass(frozen=True)
class DerivedEvaluation:
    value: float | None
    status: str


@dataclass(frozen=True)
class DerivedMetricPreview:
    total_records: int
    valid_result_count: int
    missing_input_count: int
    division_by_zero_count: int
    non_finite_result_count: int
    minimum: float | None
    maximum: float | None
    mean: float | None


def validate_derived_metric(
    profile: DatasetProfile,
    *,
    name: str,
    operation: str,
    left_column: str,
    right_column: str,
    aggregation: str,
    display_format: str,
) -> DerivedMetric:
    """Validate a restricted formula against deterministic profile metadata."""

    clean_name = name.strip()
    if not clean_name or len(clean_name) > 120 or not clean_name.isprintable():
        raise DerivedMetricError("Derived KPI name must contain 1 to 120 printable characters.")
    if clean_name.casefold() in {column.name.casefold() for column in profile.columns}:
        raise DerivedMetricError(
            "Derived KPI name must not duplicate an existing dataset column."
        )
    if operation not in OPERATIONS:
        raise DerivedMetricError("Derived KPI operation is not supported.")
    if left_column == right_column:
        raise DerivedMetricError("Derived KPI must use two different source columns.")
    for column_name in (left_column, right_column):
        column = profile.column(column_name)
        if column is None or column.inferred_type is not ColumnType.NUMERIC:
            raise DerivedMetricError(
                "Derived KPI source columns must be existing numeric columns."
            )
    if aggregation not in AGGREGATIONS:
        raise DerivedMetricError("Derived KPI aggregation is not supported.")
    if aggregation == "ratio_of_sums" and operation not in RATIO_OF_SUMS_OPERATIONS:
        raise DerivedMetricError(
            "Ratio-of-sums aggregation requires a ratio or percentage operation."
        )
    if operation in RATIO_OF_SUMS_OPERATIONS and aggregation == "sum":
        raise DerivedMetricError(
            "Ratio and percentage operations cannot use additive sum aggregation."
        )
    if display_format not in DISPLAY_FORMATS:
        raise DerivedMetricError("Derived KPI display format is not supported.")
    if operation in PERCENTAGE_OPERATIONS and display_format != "percentage":
        raise DerivedMetricError("Percentage operations must use percentage display format.")
    if operation not in PERCENTAGE_OPERATIONS and display_format == "percentage":
        raise DerivedMetricError(
            "Non-percentage operations cannot use percentage display format."
        )

    return DerivedMetric(
        schema_version=1,
        name=clean_name,
        operation=operation,
        left_column=left_column,
        right_column=right_column,
        aggregation=aggregation,
        display_format=display_format,
    )


def load_derived_metric(profile: DatasetProfile, payload: object) -> DerivedMetric:
    """Load an untrusted persisted formula and revalidate every field."""

    expected = {
        "schema_version",
        "name",
        "operation",
        "left_column",
        "right_column",
        "aggregation",
        "display_format",
        "division_by_zero",
        "missing_input",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise DerivedMetricError("Saved derived KPI has an invalid shape.")
    if payload.get("schema_version") != 1:
        raise DerivedMetricError("Saved derived KPI version is unsupported.")
    if payload.get("division_by_zero") != "return_null":
        raise DerivedMetricError("Saved derived KPI has an unsafe zero-division policy.")
    if payload.get("missing_input") != "return_null":
        raise DerivedMetricError("Saved derived KPI has an unsafe missing-input policy.")
    text_fields = (
        "name",
        "operation",
        "left_column",
        "right_column",
        "aggregation",
        "display_format",
    )
    if not all(isinstance(payload.get(field), str) for field in text_fields):
        raise DerivedMetricError("Saved derived KPI contains invalid text fields.")
    return validate_derived_metric(
        profile,
        name=payload["name"],
        operation=payload["operation"],
        left_column=payload["left_column"],
        right_column=payload["right_column"],
        aggregation=payload["aggregation"],
        display_format=payload["display_format"],
    )


def evaluate_derived_metric(
    metric: DerivedMetric, values: Mapping[str, object]
) -> DerivedEvaluation:
    """Evaluate one row or aggregate pair without executing expression text."""

    left = _finite_number(values.get(metric.left_column))
    right = _finite_number(values.get(metric.right_column))
    if left is None or right is None:
        return DerivedEvaluation(None, "missing_input")

    if metric.operation == "add":
        result = left + right
    elif metric.operation == "subtract":
        result = left - right
    elif metric.operation == "multiply":
        result = left * right
    elif metric.operation in {"ratio", "percentage_ratio", "percentage_difference"}:
        if right == 0:
            return DerivedEvaluation(None, "division_by_zero")
        if metric.operation == "ratio":
            result = left / right
        elif metric.operation == "percentage_ratio":
            result = (left / right) * 100
        else:
            result = ((left - right) / right) * 100
    elif metric.operation == "margin_percentage":
        if left == 0:
            return DerivedEvaluation(None, "division_by_zero")
        result = ((left - right) / left) * 100
    else:  # Defensive guard for objects not created by the validator.
        raise DerivedMetricError("Derived KPI operation is not supported.")

    if not math.isfinite(result):
        return DerivedEvaluation(None, "non_finite_result")
    return DerivedEvaluation(_clean(result), "valid")


def preview_derived_metric(path: Path, metric: DerivedMetric) -> DerivedMetricPreview:
    """Calculate safe preview statistics without retaining raw row values."""

    try:
        text = path.read_bytes().decode("utf-8-sig", errors="strict")
    except (OSError, UnicodeDecodeError) as error:
        raise DerivedMetricError("Retained CSV cannot be read for derived KPI preview.") from error
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    if reader.fieldnames is None:
        raise DerivedMetricError("Retained CSV has no readable header.")

    results: list[float] = []
    statuses = {"missing_input": 0, "division_by_zero": 0, "non_finite_result": 0}
    total = 0
    try:
        for row in reader:
            total += 1
            evaluation = evaluate_derived_metric(metric, row)
            if evaluation.value is None:
                statuses[evaluation.status] += 1
            else:
                results.append(evaluation.value)
    except csv.Error as error:
        raise DerivedMetricError("Retained CSV is malformed during KPI preview.") from error

    return DerivedMetricPreview(
        total_records=total,
        valid_result_count=len(results),
        missing_input_count=statuses["missing_input"],
        division_by_zero_count=statuses["division_by_zero"],
        non_finite_result_count=statuses["non_finite_result"],
        minimum=min(results) if results else None,
        maximum=max(results) if results else None,
        mean=_clean(math.fsum(results) / len(results)) if results else None,
    )


def _finite_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.casefold() in _MISSING_MARKERS:
            return None
    else:
        candidate = value
    try:
        number = float(candidate)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clean(value: float) -> float:
    rounded = round(float(value), 10)
    return 0.0 if rounded == 0 else rounded
