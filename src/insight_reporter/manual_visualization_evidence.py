"""Deterministic evidence summaries for user-created visualizations."""

from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import dataclass

from insight_reporter.manual_visualization_store import ManualVisualizationArtifact
from insight_reporter.visualization_builder import VisualizationArtifact

_MAX_OBSERVATIONS = 24
_MAX_SUPPORTING_ROWS = 25
_SAFE_SUPPORTING_KEYS = frozenset(
    {
        "aggregation",
        "bin_end",
        "bin_start",
        "category",
        "count",
        "measure",
        "measure_selector",
        "maximum",
        "median",
        "minimum",
        "q1",
        "q3",
        "record_count",
        "secondary_y",
        "series",
        "size",
        "target",
        "value",
        "x",
        "y",
    }
)


class ManualVisualizationEvidenceError(ValueError):
    """Raised when a manual chart cannot produce safe deterministic evidence."""


@dataclass(frozen=True)
class ManualVisualizationEvidence:
    schema_version: int
    id: str
    visualization_id: str
    classification: str
    title: str
    purpose: str
    purpose_source: str
    chart_type: str
    source: dict[str, object]
    source_columns: tuple[str, ...]
    required_metric_ids: tuple[str, ...]
    measures: tuple[dict[str, object], ...]
    filters: dict[str, object]
    filtered_record_count: int
    observations: tuple[dict[str, object], ...]
    supporting_data: tuple[dict[str, object], ...]
    supporting_data_omitted_count: int
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "visualization_id": self.visualization_id,
            "classification": self.classification,
            "title": self.title,
            "purpose": self.purpose,
            "purpose_source": self.purpose_source,
            "chart_type": self.chart_type,
            "source": self.source,
            "source_columns": list(self.source_columns),
            "required_metric_ids": list(self.required_metric_ids),
            "measures": list(self.measures),
            "filters": self.filters,
            "filtered_record_count": self.filtered_record_count,
            "observations": list(self.observations),
            "supporting_data": list(self.supporting_data),
            "supporting_data_omitted_count": (self.supporting_data_omitted_count),
            "limitations": list(self.limitations),
        }


def generate_manual_visualization_evidence(
    artifact: VisualizationArtifact,
    *,
    requested_insight_observations: tuple[dict[str, object], ...] = (),
) -> ManualVisualizationEvidence:
    """Calculate bounded, reproducible observations from chart supporting data."""

    if artifact.visualization_id is None:
        raise ManualVisualizationEvidenceError(
            "Manual visualization evidence requires a saved visualization."
        )
    rows = tuple(dict(row) for row in artifact.supporting_data)
    if not rows:
        raise ManualVisualizationEvidenceError("Manual visualization has no supporting data.")
    observations: list[dict[str, object]] = []
    if artifact.spec.chart_type in {
        "time_area",
        "time_area_stacked",
        "category_bar",
        "category_bar_horizontal",
        "category_bar_stacked",
        "pareto",
        "donut",
        "heatmap",
        "waterfall",
        "funnel",
        "combo",
        "scorecard",
        "time_line",
    }:
        observations.extend(_aggregate_observations(artifact, rows))
    elif artifact.spec.chart_type == "scatter":
        observation = _scatter_observation(artifact, rows)
        if observation is not None:
            observations.append(observation)
    elif artifact.spec.chart_type in {"histogram", "box"}:
        observation = _distribution_observation(artifact, rows)
        if observation is not None:
            observations.append(observation)
    observations.extend(dict(observation) for observation in requested_insight_observations)

    limitations = [
        "Observations are descriptive and do not establish causation.",
    ]
    if artifact.classification == "supplementary":
        limitations.append(
            "This visualization is supplementary and is not a confirmed KPI finding."
        )
    if not artifact.spec.purpose:
        limitations.append("No user-provided visualization question or purpose was supplied.")
    if (
        artifact.spec.filter_column is not None
        or artifact.spec.date_start is not None
        or artifact.spec.date_end is not None
    ):
        limitations.append("Observations apply only to the configured visualization filters.")
    if artifact.spec.chart_type in {
        "category_bar",
        "category_bar_horizontal",
        "category_bar_stacked",
        "pareto",
        "donut",
        "heatmap",
        "waterfall",
        "funnel",
        "combo",
    }:
        limitations.append("Displayed rankings may be limited by the configured Top-N setting.")
    if len(observations) > _MAX_OBSERVATIONS:
        observations = observations[:_MAX_OBSERVATIONS]
        limitations.append(
            "Additional deterministic chart observations were omitted to keep "
            "the report input bounded."
        )

    safe_rows = tuple(_safe_supporting_row(row) for row in rows[:_MAX_SUPPORTING_ROWS])
    omitted_count = max(0, len(rows) - len(safe_rows))
    if omitted_count:
        limitations.append(
            f"{omitted_count} supporting chart rows were omitted from the bounded report input."
        )
    required_metric_ids = tuple(
        measure.selector.removeprefix("metric:")
        for measure in artifact.measures
        if measure.selector.startswith("metric:")
    )
    return ManualVisualizationEvidence(
        schema_version=1,
        id=_manual_evidence_id(
            artifact.dataset_id,
            artifact.visualization_id,
        ),
        visualization_id=artifact.visualization_id,
        classification=artifact.classification,
        title=artifact.spec.title,
        purpose=artifact.spec.purpose,
        purpose_source=("user_provided" if artifact.spec.purpose else "not_provided"),
        chart_type=artifact.spec.chart_type,
        source=dict(artifact.source),
        source_columns=artifact.source_columns,
        required_metric_ids=required_metric_ids,
        measures=tuple(measure.to_dict() for measure in artifact.measures),
        filters=_filters(artifact),
        filtered_record_count=artifact.filtered_record_count,
        observations=tuple(observations),
        supporting_data=safe_rows,
        supporting_data_omitted_count=omitted_count,
        limitations=tuple(limitations),
    )


def generate_manual_board_evidence(
    artifact: ManualVisualizationArtifact,
    *,
    requested_insight_observations: tuple[dict[str, object], ...] = (),
) -> ManualVisualizationEvidence:
    """Build report-safe evidence from a saved drag-and-drop board chart."""

    raw_points = artifact.preview.get("points")
    if not isinstance(raw_points, list) or not raw_points or not all(
        isinstance(point, dict) for point in raw_points
    ):
        raise ManualVisualizationEvidenceError(
            "Manual-board visualization has no valid supporting data."
        )
    rows = tuple(dict(point) for point in raw_points)
    observations = _manual_board_observations(artifact, rows)
    observations.extend(
        dict(observation) for observation in requested_insight_observations
    )
    limitations = [
        "Observations are descriptive and do not establish causation.",
        "The chart uses the manual board's current Sum or row-value aggregation.",
    ]
    if artifact.preview.get("truncated") is True:
        limitations.append(
            "The saved chart preview was bounded, so some source groups or rows are not displayed."
        )
    if artifact.chart_type in {
        "column",
        "bar",
        "pie",
        "donut",
        "pareto",
        "funnel",
        "treemap",
        "radar",
        "stacked_column",
        "stacked_bar",
        "grouped_column",
        "stacked_100_column",
        "stacked_100_bar",
        "heatmap",
    }:
        limitations.append("Displayed rankings may be limited by the board preview bounds.")
    if len(observations) > _MAX_OBSERVATIONS:
        observations = observations[:_MAX_OBSERVATIONS]
        limitations.append(
            "Additional deterministic chart observations were omitted to keep "
            "the report input bounded."
        )
    safe_rows = tuple(_safe_supporting_row(row) for row in rows[:_MAX_SUPPORTING_ROWS])
    omitted_count = max(0, len(rows) - len(safe_rows))
    if omitted_count:
        limitations.append(
            f"{omitted_count} supporting chart rows were omitted from the bounded report input."
        )
    fields = tuple(
        field
        for role in ("x", "y", "series", "size", "secondary_y")
        if (field := artifact.fields.get(role)) is not None
    )
    measures = tuple(
        {
            "role": role,
            "label": field,
            "selector": f"column:{field}",
            "aggregation": str(artifact.preview.get("aggregation", "")),
        }
        for role in ("y", "secondary_y", "size")
        if (field := artifact.fields.get(role)) is not None
    )
    if not measures:
        label = str(artifact.preview.get("y_label", "Record count"))
        measures = (
            {
                "role": "y",
                "label": label,
                "selector": "count:records",
                "aggregation": str(artifact.preview.get("aggregation", "Record count")),
            },
        )
    record_count = artifact.preview.get("record_count", len(rows))
    if isinstance(record_count, bool) or not isinstance(record_count, int) or record_count < 0:
        record_count = len(rows)
    return ManualVisualizationEvidence(
        schema_version=1,
        id=_manual_evidence_id(artifact.dataset_id, artifact.visualization_id),
        visualization_id=artifact.visualization_id,
        classification="manual_board",
        title=artifact.title,
        purpose="",
        purpose_source="not_provided",
        chart_type=artifact.chart_type,
        source={"sha256": artifact.source_sha256},
        source_columns=tuple(dict.fromkeys(fields)),
        required_metric_ids=(),
        measures=measures,
        filters={
            "category_column": None,
            "category_mode": None,
            "category_values": [],
            "date_start": None,
            "date_end": None,
            "top_n": None,
        },
        filtered_record_count=record_count,
        observations=tuple(observations),
        supporting_data=safe_rows,
        supporting_data_omitted_count=omitted_count,
        limitations=tuple(limitations),
    )


def _manual_board_observations(
    artifact: ManualVisualizationArtifact,
    rows: tuple[dict[str, object], ...],
) -> list[dict[str, object]]:
    chart_type = artifact.chart_type
    y_label = str(artifact.preview.get("y_label", "Value"))
    if chart_type in {"scatter", "bubble"}:
        pairs = [
            (x, y)
            for row in rows
            if (x := _number(row.get("x"))) is not None
            and (y := _number(row.get("y"))) is not None
        ]
        if len(pairs) < 3:
            return [{
                "type": "association_skipped",
                "measure": y_label,
                "observation": {"reason": "fewer_than_three_pairs"},
                "record_count": len(pairs),
                "confidence": "low",
            }]
        x_values = [pair[0] for pair in pairs]
        y_values = [pair[1] for pair in pairs]
        x_mean = statistics.fmean(x_values)
        y_mean = statistics.fmean(y_values)
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
        denominator = math.sqrt(
            sum((x - x_mean) ** 2 for x in x_values)
            * sum((y - y_mean) ** 2 for y in y_values)
        )
        coefficient = None if denominator == 0 else numerator / denominator
        return [{
            "type": "numeric_association",
            "measure": y_label,
            "observation": {
                "x_column": artifact.fields.get("x"),
                "y_column": artifact.fields.get("y"),
                "coefficient": coefficient,
                "label": "association_not_causation",
                "constant_input": denominator == 0,
            },
            "record_count": len(pairs),
            "confidence": "high" if coefficient is not None else "low",
        }]
    if chart_type == "box":
        return [
            {
                "type": "displayed_distribution",
                "measure": y_label,
                "observation": {
                    "x": row.get("x"),
                    "minimum": row.get("minimum"),
                    "q1": row.get("q1"),
                    "median": row.get("median"),
                    "q3": row.get("q3"),
                    "maximum": row.get("maximum"),
                    "count": row.get("count"),
                },
                "record_count": int(row.get("count", 0)),
                "confidence": "high",
            }
            for row in rows
        ]
    numeric_rows = [row for row in rows if _number(row.get("y")) is not None]
    if not numeric_rows:
        return []
    highest = max(numeric_rows, key=lambda row: float(row["y"]))
    lowest = min(numeric_rows, key=lambda row: float(row["y"]))
    observations: list[dict[str, object]] = [{
        "type": "displayed_extremes",
        "measure": y_label,
        "observation": {
            "highest": {
                "x": highest.get("x"),
                "series": highest.get("series"),
                "value": highest["y"],
            },
            "lowest": {"x": lowest.get("x"), "series": lowest.get("series"), "value": lowest["y"]},
        },
        "record_count": len(numeric_rows),
        "confidence": "high",
    }]
    if chart_type in {"line", "area", "multi_line"}:
        for series in tuple(dict.fromkeys(str(row.get("series") or "") for row in numeric_rows)):
            points = [row for row in numeric_rows if str(row.get("series") or "") == series]
            if len(points) < 2:
                continue
            first, last = points[0], points[-1]
            first_value = float(first["y"])
            last_value = float(last["y"])
            change = last_value - first_value
            observations.append({
                "type": "displayed_time_change",
                "measure": y_label,
                "observation": {
                    "series": series or None,
                    "first_period": first.get("x"),
                    "first_value": first_value,
                    "last_period": last.get("x"),
                    "last_value": last_value,
                    "absolute_change": change,
                    "percentage_change": (
                        None if first_value == 0 else change / abs(first_value) * 100
                    ),
                    "division_by_zero": first_value == 0,
                },
                "record_count": len(points),
                "confidence": "high",
            })
    if chart_type in {"gauge", "bullet"}:
        target = _number(artifact.preview.get("target"))
        actual = float(numeric_rows[0]["y"])
        if target is not None:
            observations.append({
                "type": "target_comparison",
                "measure": y_label,
                "observation": {
                    "current_value": actual,
                    "target": target,
                    "gap_to_target": actual - target,
                    "percentage_of_target": actual / target * 100 if target else None,
                },
                "record_count": int(artifact.preview.get("record_count", len(rows))),
                "confidence": "high",
            })
    if chart_type == "pareto":
        total = math.fsum(float(row["y"]) for row in numeric_rows)
        if total:
            observations.append({
                "type": "top_contribution",
                "measure": y_label,
                "observation": {
                    "top_category": highest.get("x"),
                    "top_value": highest["y"],
                    "share_percentage": float(highest["y"]) / total * 100,
                    "pareto_line_mode": artifact.settings.get("pareto_line"),
                },
                "record_count": len(numeric_rows),
                "confidence": "high",
            })
    return observations


def _aggregate_observations(
    artifact: VisualizationArtifact,
    rows: tuple[dict[str, object], ...],
) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    labels = {measure.selector: measure.label for measure in artifact.measures}
    selectors = tuple(dict.fromkeys(str(row.get("measure_selector", "")) for row in rows))
    for selector in selectors:
        selected = [
            row
            for row in rows
            if str(row.get("measure_selector", "")) == selector
            and _number(row.get("value")) is not None
        ]
        if not selected:
            continue
        highest = max(selected, key=lambda row: float(row["value"]))
        lowest = min(selected, key=lambda row: float(row["value"]))
        observations.append(
            {
                "type": "displayed_extremes",
                "measure": labels.get(selector, str(highest.get("measure", ""))),
                "observation": {
                    "highest": _point(highest),
                    "lowest": _point(lowest),
                },
                "record_count": len(selected),
                "confidence": "high",
            }
        )
        if artifact.spec.chart_type in {
            "time_line",
            "time_area",
            "time_area_stacked",
        }:
            observations.extend(
                _time_changes(
                    selected,
                    measure=labels.get(
                        selector,
                        str(highest.get("measure", "")),
                    ),
                )
            )
    return observations


def _time_changes(
    rows: list[dict[str, object]],
    *,
    measure: str,
) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    series_values = tuple(dict.fromkeys(str(row.get("series") or "") for row in rows))
    for series in series_values:
        points = sorted(
            (
                row
                for row in rows
                if str(row.get("series") or "") == series and isinstance(row.get("x"), str)
            ),
            key=lambda row: str(row["x"]),
        )
        if len(points) < 2:
            continue
        first = points[0]
        last = points[-1]
        first_value = float(first["value"])
        last_value = float(last["value"])
        absolute_change = last_value - first_value
        percentage_change = None if first_value == 0 else (absolute_change / abs(first_value)) * 100
        observations.append(
            {
                "type": "displayed_time_change",
                "measure": measure,
                "observation": {
                    "series": series or None,
                    "first_period": first["x"],
                    "first_value": first_value,
                    "last_period": last["x"],
                    "last_value": last_value,
                    "absolute_change": absolute_change,
                    "percentage_change": percentage_change,
                    "division_by_zero": first_value == 0,
                },
                "record_count": len(points),
                "confidence": "high",
            }
        )
    return observations


def _scatter_observation(
    artifact: VisualizationArtifact,
    rows: tuple[dict[str, object], ...],
) -> dict[str, object] | None:
    pairs = [
        (x, y)
        for row in rows
        if (x := _number(row.get("x"))) is not None and (y := _number(row.get("y"))) is not None
    ]
    if len(pairs) < 3:
        return {
            "type": "association_skipped",
            "measure": artifact.measures[0].label,
            "observation": {"reason": "fewer_than_three_pairs"},
            "record_count": len(pairs),
            "confidence": "low",
        }
    x_values = [pair[0] for pair in pairs]
    y_values = [pair[1] for pair in pairs]
    x_mean = statistics.fmean(x_values)
    y_mean = statistics.fmean(y_values)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    denominator = math.sqrt(
        sum((x - x_mean) ** 2 for x in x_values) * sum((y - y_mean) ** 2 for y in y_values)
    )
    coefficient = None if denominator == 0 else numerator / denominator
    return {
        "type": "numeric_association",
        "measure": artifact.measures[0].label,
        "observation": {
            "x_column": artifact.spec.x_column,
            "coefficient": coefficient,
            "label": "association_not_causation",
            "constant_input": denominator == 0,
        },
        "record_count": len(pairs),
        "confidence": "high" if coefficient is not None else "low",
    }


def _distribution_observation(
    artifact: VisualizationArtifact,
    rows: tuple[dict[str, object], ...],
) -> dict[str, object] | None:
    values = sorted(value for row in rows if (value := _number(row.get("value"))) is not None)
    if not values:
        return None
    lower, upper = _quartile_halves(values)
    q1 = statistics.median(lower)
    q3 = statistics.median(upper)
    iqr = q3 - q1
    low_fence = q1 - 1.5 * iqr
    high_fence = q3 + 1.5 * iqr
    outliers = [value for value in values if value < low_fence or value > high_fence]
    return {
        "type": "displayed_distribution",
        "measure": artifact.measures[0].label,
        "observation": {
            "minimum": values[0],
            "maximum": values[-1],
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "q1": q1,
            "q3": q3,
            "iqr": iqr,
            "lower_fence": low_fence,
            "upper_fence": high_fence,
            "outlier_count": len(outliers),
            "outlier_values": outliers[:10],
        },
        "record_count": len(values),
        "confidence": "high",
    }


def _quartile_halves(
    values: list[float],
) -> tuple[list[float], list[float]]:
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[: midpoint + 1], values[midpoint:]
    return values[:midpoint], values[midpoint:]


def _point(row: dict[str, object]) -> dict[str, object]:
    return {
        "x": row.get("x"),
        "series": row.get("series"),
        "value": float(row["value"]),
        "record_count": row.get("record_count"),
    }


def _filters(artifact: VisualizationArtifact) -> dict[str, object]:
    spec = artifact.spec
    return {
        "category_column": spec.filter_column,
        "category_mode": spec.filter_mode,
        "category_values": list(spec.filter_values),
        "date_start": spec.date_start,
        "date_end": spec.date_end,
        "top_n": (
            spec.top_n
            if spec.chart_type
            in {
                "category_bar",
                "category_bar_horizontal",
                "category_bar_stacked",
                "pareto",
                "donut",
                "heatmap",
                "waterfall",
                "funnel",
                "combo",
            }
            else None
        ),
    }


def _safe_supporting_row(
    row: dict[str, object],
) -> dict[str, object]:
    return {
        key: value
        for key, value in row.items()
        if key in _SAFE_SUPPORTING_KEYS
        and (value is None or isinstance(value, str | int | float) and not isinstance(value, bool))
    }


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _manual_evidence_id(dataset_id: str, visualization_id: str) -> str:
    digest = hashlib.sha256(f"{dataset_id}:{visualization_id}".encode()).hexdigest()[:16].upper()
    return f"MVE-{digest}"
