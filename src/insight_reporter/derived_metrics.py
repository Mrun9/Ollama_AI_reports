"""Restricted, deterministic derived-KPI definitions and calculations."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from insight_reporter.dataset_profile import ColumnType, DatasetProfile
from insight_reporter.dataset_view import CsvDatasetView, DatasetView, DatasetViewError
from insight_reporter.formula_engine import (
    ROW_AGGREGATIONS,
    FormulaError,
    ParsedFormula,
    aggregate_row_values,
    evaluate_aggregate_formula,
    evaluate_row_formula,
    load_parsed_formula,
    parse_formula,
)

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
AGGREGATIONS = frozenset({"sum", "mean", "ratio_of_sums", *ROW_AGGREGATIONS, "formula"})
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
    formula: ParsedFormula | None = None

    @property
    def source_columns(self) -> tuple[str, ...]:
        if self.formula is not None:
            return self.formula.source_columns
        return (self.left_column, self.right_column)

    @property
    def formula_label(self) -> str:
        if self.formula is not None:
            return self.formula.formula_label
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

    @property
    def calculation_level(self) -> str:
        return self.formula.calculation_level if self.formula is not None else (
            "aggregate" if self.aggregation == "ratio_of_sums" else "row"
        )

    def to_dict(self) -> dict[str, object]:
        if self.schema_version == 1:
            return {
                "schema_version": 1,
                "name": self.name,
                "operation": self.operation,
                "left_column": self.left_column,
                "right_column": self.right_column,
                "aggregation": self.aggregation,
                "display_format": self.display_format,
                "division_by_zero": "return_null",
                "missing_input": "return_null",
            }
        assert self.formula is not None
        return {
            "schema_version": 2,
            "name": self.name,
            "formula": self.formula.to_dict(),
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
    """Validate a legacy two-column formula and retain backward compatibility."""

    clean_name = _validate_name(profile, name)
    if operation not in OPERATIONS:
        raise DerivedMetricError("Derived KPI operation is not supported.")
    if left_column == right_column:
        raise DerivedMetricError("Derived KPI must use two different source columns.")
    _validate_numeric_columns(profile, (left_column, right_column))
    if aggregation not in {"sum", "mean", "ratio_of_sums"}:
        raise DerivedMetricError("Derived KPI aggregation is not supported.")
    if aggregation == "ratio_of_sums" and operation not in RATIO_OF_SUMS_OPERATIONS:
        raise DerivedMetricError(
            "Ratio-of-sums aggregation requires a ratio or percentage operation."
        )
    if operation in RATIO_OF_SUMS_OPERATIONS and aggregation == "sum":
        raise DerivedMetricError(
            "Ratio and percentage operations cannot use additive sum aggregation."
        )
    _validate_display_format(operation, display_format)
    return DerivedMetric(
        schema_version=1,
        name=clean_name,
        operation=operation,
        left_column=left_column,
        right_column=right_column,
        aggregation=aggregation,
        display_format=display_format,
    )


def validate_formula_metric(
    profile: DatasetProfile,
    *,
    name: str,
    formula: str,
    calculation_level: str,
    aggregation: str,
    display_format: str,
    source_id: str,
) -> DerivedMetric:
    """Validate a multi-variable formula using the restricted parser."""

    clean_name = _validate_name(profile, name)
    try:
        parsed = parse_formula(
            formula,
            profile=profile,
            source_id=source_id,
            calculation_level=calculation_level,
        )
    except FormulaError as error:
        raise DerivedMetricError(str(error)) from error
    if calculation_level == "row":
        if aggregation not in ROW_AGGREGATIONS:
            raise DerivedMetricError(
                "Row formulas require sum, mean, median, min, or max aggregation."
            )
    elif aggregation != "formula":
        raise DerivedMetricError(
            "Aggregate formulas must use formula aggregation."
        )
    if display_format not in DISPLAY_FORMATS:
        raise DerivedMetricError("Derived KPI display format is not supported.")
    return DerivedMetric(
        schema_version=2,
        name=clean_name,
        operation="formula",
        left_column="",
        right_column="",
        aggregation=aggregation,
        display_format=display_format,
        formula=parsed,
    )


def load_derived_metric(
    profile: DatasetProfile, payload: object, *, source_id: str | None = None
) -> DerivedMetric:
    """Load an untrusted persisted formula and revalidate every field."""

    if not isinstance(payload, dict):
        raise DerivedMetricError("Saved derived KPI has an invalid shape.")
    version = payload.get("schema_version")
    if version == 1:
        return _load_legacy_metric(profile, payload)
    if version != 2:
        raise DerivedMetricError("Saved derived KPI version is unsupported.")
    expected = {
        "schema_version",
        "name",
        "formula",
        "aggregation",
        "display_format",
        "division_by_zero",
        "missing_input",
    }
    if set(payload) != expected:
        raise DerivedMetricError("Saved derived KPI has an invalid shape.")
    if payload.get("division_by_zero") != "return_null":
        raise DerivedMetricError("Saved derived KPI has an unsafe zero-division policy.")
    if payload.get("missing_input") != "return_null":
        raise DerivedMetricError("Saved derived KPI has an unsafe missing-input policy.")
    name = payload.get("name")
    aggregation = payload.get("aggregation")
    display_format = payload.get("display_format")
    if not all(isinstance(value, str) for value in (name, aggregation, display_format)):
        raise DerivedMetricError("Saved derived KPI contains invalid text fields.")
    formula_payload = payload.get("formula")
    if source_id is None:
        references = (
            formula_payload.get("source_references")
            if isinstance(formula_payload, dict)
            else None
        )
        first = references[0] if isinstance(references, list) and references else None
        source_id = first.get("source_id") if isinstance(first, dict) else None
    if not isinstance(source_id, str):
        raise DerivedMetricError("Saved derived KPI source is invalid.")
    try:
        parsed = load_parsed_formula(
            formula_payload,
            profile=profile,
            source_id=source_id,
        )
    except FormulaError as error:
        raise DerivedMetricError(str(error)) from error
    return validate_formula_metric(
        profile,
        name=name,
        formula=parsed.formula_label,
        calculation_level=parsed.calculation_level,
        aggregation=aggregation,
        display_format=display_format,
        source_id=source_id,
    )


def evaluate_derived_metric(
    metric: DerivedMetric, values: Mapping[str, object]
) -> DerivedEvaluation:
    """Evaluate one row without executing expression text."""

    if metric.formula is not None:
        if metric.formula.calculation_level != "row":
            return DerivedEvaluation(None, "aggregate_only")
        result = evaluate_row_formula(metric.formula, dict(values))
        return DerivedEvaluation(result.value, result.status)

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
    else:
        raise DerivedMetricError("Derived KPI operation is not supported.")
    if not math.isfinite(result):
        return DerivedEvaluation(None, "non_finite_result")
    return DerivedEvaluation(_clean(result), "valid")


def aggregate_derived_metric(
    metric: DerivedMetric, rows: tuple[Mapping[str, object], ...]
) -> DerivedEvaluation:
    """Calculate one derived KPI value for a deterministic group of rows."""

    if not rows:
        return DerivedEvaluation(None, "missing_input")
    if metric.formula is not None and metric.formula.calculation_level == "aggregate":
        result = evaluate_aggregate_formula(
            metric.formula, tuple(dict(row) for row in rows)
        )
        return DerivedEvaluation(result.value, result.status)

    evaluations = [evaluate_derived_metric(metric, row) for row in rows]
    valid = [item.value for item in evaluations if item.value is not None]
    if metric.aggregation == "ratio_of_sums":
        left_values = [_finite_number(row.get(metric.left_column)) for row in rows]
        right_values = [_finite_number(row.get(metric.right_column)) for row in rows]
        if any(value is None for value in (*left_values, *right_values)):
            return DerivedEvaluation(None, "missing_input")
        aggregate_inputs = {
            metric.left_column: math.fsum(
                value for value in left_values if value is not None
            ),
            metric.right_column: math.fsum(
                value for value in right_values if value is not None
            ),
        }
        return evaluate_derived_metric(metric, aggregate_inputs)
    if not valid:
        status = evaluations[0].status if evaluations else "missing_input"
        return DerivedEvaluation(None, status)
    try:
        value = aggregate_row_values(
            [float(item) for item in valid],
            metric.aggregation,
        )
    except FormulaError as error:
        raise DerivedMetricError(str(error)) from error
    return DerivedEvaluation(value, "valid" if value is not None else "non_finite_result")


def preview_derived_metric(
    source: Path | DatasetView, metric: DerivedMetric
) -> DerivedMetricPreview:
    """Calculate safe preview statistics without retaining raw row values."""

    try:
        view = CsvDatasetView.from_path(source) if isinstance(source, Path) else source
        rows = view.iter_rows()
    except DatasetViewError as error:
        raise DerivedMetricError(str(error)) from error
    statuses = {"missing_input": 0, "division_by_zero": 0, "non_finite_result": 0}

    if metric.formula is not None and metric.calculation_level == "aggregate":
        evaluation = aggregate_derived_metric(
            metric, tuple(row.values for row in rows)
        )
        if evaluation.value is None:
            if evaluation.status in statuses:
                statuses[evaluation.status] += 1
            results: list[float] = []
        else:
            results = [evaluation.value]
    else:
        results = []
        for row in rows:
            evaluation = evaluate_derived_metric(metric, row.values)
            if evaluation.value is None:
                if evaluation.status in statuses:
                    statuses[evaluation.status] += 1
            else:
                results.append(evaluation.value)

    return DerivedMetricPreview(
        total_records=len(rows),
        valid_result_count=len(results),
        missing_input_count=statuses["missing_input"],
        division_by_zero_count=statuses["division_by_zero"],
        non_finite_result_count=statuses["non_finite_result"],
        minimum=min(results) if results else None,
        maximum=max(results) if results else None,
        mean=_clean(math.fsum(results) / len(results)) if results else None,
    )


def convert_legacy_metric_to_formula(
    profile: DatasetProfile,
    metric: DerivedMetric,
    *,
    source_id: str,
) -> DerivedMetric:
    """Convert a two-column v1 metric into the v2 restricted formula model."""

    if metric.formula is not None:
        return metric
    left = _formula_column(metric.left_column)
    right = _formula_column(metric.right_column)
    scale = " * 100" if metric.operation in PERCENTAGE_OPERATIONS else ""
    if metric.aggregation == "ratio_of_sums":
        aggregate_left = f"SUM({left})"
        aggregate_right = f"SUM({right})"
        formulas = {
            "ratio": f"{aggregate_left} / {aggregate_right}",
            "percentage_ratio": f"({aggregate_left} / {aggregate_right}) * 100",
            "percentage_difference": (
                f"(({aggregate_left} - {aggregate_right}) / {aggregate_right}) * 100"
            ),
            "margin_percentage": (
                f"(({aggregate_left} - {aggregate_right}) / {aggregate_left}) * 100"
            ),
        }
        formula = formulas[metric.operation]
        level = "aggregate"
        aggregation = "formula"
    else:
        operators = {
            "add": "+",
            "subtract": "-",
            "multiply": "*",
            "ratio": "/",
            "percentage_ratio": "/",
            "percentage_difference": "-",
            "margin_percentage": "-",
        }
        if metric.operation == "percentage_difference":
            formula = f"(({left} - {right}) / {right}) * 100"
        elif metric.operation == "margin_percentage":
            formula = f"(({left} - {right}) / {left}) * 100"
        else:
            formula = f"({left} {operators[metric.operation]} {right}){scale}"
        level = "row"
        aggregation = metric.aggregation
    return validate_formula_metric(
        profile,
        name=metric.name,
        formula=formula,
        calculation_level=level,
        aggregation=aggregation,
        display_format=metric.display_format,
        source_id=source_id,
    )


def _load_legacy_metric(profile: DatasetProfile, payload: dict[object, object]) -> DerivedMetric:
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
    if set(payload) != expected:
        raise DerivedMetricError("Saved derived KPI has an invalid shape.")
    if payload.get("division_by_zero") != "return_null":
        raise DerivedMetricError("Saved derived KPI has an unsafe zero-division policy.")
    if payload.get("missing_input") != "return_null":
        raise DerivedMetricError("Saved derived KPI has an unsafe missing-input policy.")
    fields = (
        payload.get("name"),
        payload.get("operation"),
        payload.get("left_column"),
        payload.get("right_column"),
        payload.get("aggregation"),
        payload.get("display_format"),
    )
    if not all(isinstance(value, str) for value in fields):
        raise DerivedMetricError("Saved derived KPI contains invalid text fields.")
    name, operation, left, right, aggregation, display = fields
    return validate_derived_metric(
        profile,
        name=name,
        operation=operation,
        left_column=left,
        right_column=right,
        aggregation=aggregation,
        display_format=display,
    )


def _validate_name(profile: DatasetProfile, name: str) -> str:
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 120 or not clean_name.isprintable():
        raise DerivedMetricError("Derived KPI name must contain 1 to 120 printable characters.")
    if clean_name.casefold() in {column.name.casefold() for column in profile.columns}:
        raise DerivedMetricError(
            "Derived KPI name must not duplicate an existing dataset column."
        )
    return clean_name


def _validate_numeric_columns(
    profile: DatasetProfile, columns: tuple[str, ...]
) -> None:
    for column_name in columns:
        column = profile.column(column_name)
        if column is None or column.inferred_type is not ColumnType.NUMERIC:
            raise DerivedMetricError(
                "Derived KPI source columns must be existing numeric columns."
            )


def _validate_display_format(operation: str, display_format: str) -> None:
    if display_format not in DISPLAY_FORMATS:
        raise DerivedMetricError("Derived KPI display format is not supported.")
    if operation in PERCENTAGE_OPERATIONS and display_format != "percentage":
        raise DerivedMetricError("Percentage operations must use percentage display format.")
    if operation not in PERCENTAGE_OPERATIONS and display_format == "percentage":
        raise DerivedMetricError(
            "Non-percentage operations cannot use percentage display format."
        )


def _finite_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        candidate: object = value.strip()
        if str(candidate).casefold() in _MISSING_MARKERS:
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


def _formula_column(column: str) -> str:
    return f"[{column.replace(']', ']]')}]"
