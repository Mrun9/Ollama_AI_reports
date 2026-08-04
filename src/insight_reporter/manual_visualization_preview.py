"""Bounded preview data for the dependency-free manual visualization board."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from insight_reporter.dataset_profile import ColumnProfile, ColumnType, DatasetProfile
from insight_reporter.dataset_view import DatasetView

MANUAL_CHART_TYPES = frozenset(
    {
        "auto",
        "column",
        "bar",
        "stacked_column",
        "stacked_bar",
        "grouped_column",
        "stacked_100_column",
        "stacked_100_bar",
        "line",
        "area",
        "scatter",
        "bubble",
        "multi_line",
        "combo",
        "pie",
        "donut",
        "histogram",
        "card",
        "table",
        "pareto",
        "waterfall",
        "funnel",
        "treemap",
        "box",
        "heatmap",
        "radar",
        "gauge",
        "bullet",
    }
)
_MISSING_MARKERS = frozenset({"", "na", "n/a", "null", "none", "nan"})
_MAX_GROUPS = 16
_MAX_DONUT_GROUPS = 8
_MAX_STACK_GROUPS = 12
_MAX_STACK_SERIES = 8
_MAX_SCATTER_POINTS = 250
_HISTOGRAM_BINS = 10


class ManualVisualizationPreviewError(ValueError):
    """Raised when fields cannot produce a safe manual-board preview."""


def build_manual_visualization_preview(
    view: DatasetView,
    profile: DatasetProfile,
    *,
    x_column: str | None,
    y_column: str | None,
    series_column: str | None,
    size_column: str | None,
    secondary_y_column: str | None,
    target_value: str | None,
    requested_chart: str,
) -> dict[str, Any]:
    """Return bounded chart data after validating requested source columns."""

    if requested_chart not in MANUAL_CHART_TYPES:
        raise ManualVisualizationPreviewError("That visualization type is not supported.")
    x_profile = _column(profile, x_column, axis="X")
    y_profile = _column(profile, y_column, axis="Y")
    series_profile = _column(profile, series_column, axis="Legend")
    size_profile = _column(profile, size_column, axis="Size")
    secondary_y_profile = _column(profile, secondary_y_column, axis="Secondary Y")
    if (
        x_profile is None
        and y_profile is None
        and series_profile is None
        and size_profile is None
        and secondary_y_profile is None
    ):
        raise ManualVisualizationPreviewError(
            "Add a field to the X-axis or Y-axis first."
        )

    chart_type = (
        _recommended_chart(
            x_profile,
            y_profile,
            series_profile,
            size_profile,
            secondary_y_profile,
        )
        if requested_chart == "auto"
        else requested_chart
    )
    rows = view.iter_rows()

    if chart_type == "scatter":
        return _scatter_preview(rows, x_profile=x_profile, y_profile=y_profile)
    if chart_type == "bubble":
        return _bubble_preview(
            rows,
            x_profile=x_profile,
            y_profile=y_profile,
            size_profile=size_profile,
        )
    if chart_type == "combo":
        return _combo_preview(
            rows,
            x_profile=x_profile,
            y_profile=y_profile,
            secondary_y_profile=secondary_y_profile,
        )
    if chart_type in {"gauge", "bullet"}:
        measure = y_profile or x_profile
        return _target_preview(
            rows,
            measure=measure,
            target_value=target_value,
            chart_type=chart_type,
        )
    if chart_type == "histogram":
        measure = y_profile or x_profile
        return _histogram_preview(rows, measure=measure)
    if chart_type == "card":
        measure = y_profile or x_profile
        return _card_preview(rows, measure=measure)
    if chart_type == "box":
        return _box_preview(
            rows,
            x_profile=x_profile,
            y_profile=y_profile,
        )
    if chart_type in {
        "stacked_column",
        "stacked_bar",
        "grouped_column",
        "stacked_100_column",
        "stacked_100_bar",
        "multi_line",
        "heatmap",
    }:
        return _stacked_preview(
            rows,
            x_profile=x_profile,
            y_profile=y_profile,
            series_profile=series_profile,
            chart_type=chart_type,
        )
    return _grouped_preview(
        rows,
        x_profile=x_profile,
        y_profile=y_profile,
        chart_type=chart_type,
    )


def _column(
    profile: DatasetProfile,
    name: str | None,
    *,
    axis: str,
) -> ColumnProfile | None:
    if not name:
        return None
    column = profile.column(name)
    if column is None or column.is_empty:
        raise ManualVisualizationPreviewError(f"The selected {axis}-axis field is unavailable.")
    return column


def _recommended_chart(
    x_profile: ColumnProfile | None,
    y_profile: ColumnProfile | None,
    series_profile: ColumnProfile | None,
    size_profile: ColumnProfile | None,
    secondary_y_profile: ColumnProfile | None,
) -> str:
    if size_profile is not None:
        if x_profile is None or y_profile is None:
            raise ManualVisualizationPreviewError(
                "A Size field needs numeric X-axis and Y-axis fields."
            )
        return "bubble"
    if secondary_y_profile is not None:
        if x_profile is None or y_profile is None:
            raise ManualVisualizationPreviewError(
                "A Secondary Y field needs X-axis and Y-axis fields."
            )
        return "combo"
    if series_profile is not None:
        if x_profile is None or y_profile is None:
            raise ManualVisualizationPreviewError(
                "A Legend needs both X-axis and Y-axis fields."
            )
        return "stacked_column"
    if x_profile is not None and y_profile is not None:
        if _is_numeric(x_profile) and _is_numeric(y_profile):
            return "scatter"
        if x_profile.inferred_type is ColumnType.DATETIME and _is_numeric(y_profile):
            return "line"
        if _is_numeric(y_profile):
            return "column"
        raise ManualVisualizationPreviewError(
            "The Y-axis needs a numeric field for this field combination."
        )
    only_field = y_profile or x_profile
    return "histogram" if only_field is not None and _is_numeric(only_field) else "column"


def _card_preview(
    rows: tuple[Any, ...],
    *,
    measure: ColumnProfile | None,
) -> dict[str, Any]:
    if measure is None or not _is_numeric(measure):
        raise ManualVisualizationPreviewError("A KPI card needs one numeric field.")
    values = [
        value
        for row in rows
        if (value := _number(row.values.get(measure.name, ""))) is not None
    ]
    if not values:
        raise ManualVisualizationPreviewError(
            "The selected field has no numeric values to summarize."
        )
    return {
        "chart_type": "card",
        "x_label": "",
        "y_label": measure.name,
        "aggregation": f"Sum of {measure.name}",
        "record_count": len(values),
        "truncated": False,
        "points": [{"x": measure.name, "y": math.fsum(values)}],
    }


def _scatter_preview(
    rows: tuple[Any, ...],
    *,
    x_profile: ColumnProfile | None,
    y_profile: ColumnProfile | None,
) -> dict[str, Any]:
    if x_profile is None or y_profile is None:
        raise ManualVisualizationPreviewError("A scatter plot needs numeric X and Y fields.")
    if not _is_numeric(x_profile) or not _is_numeric(y_profile):
        raise ManualVisualizationPreviewError("A scatter plot supports numeric fields only.")
    points: list[dict[str, float]] = []
    valid_count = 0
    for row in rows:
        x_value = _number(row.values.get(x_profile.name, ""))
        y_value = _number(row.values.get(y_profile.name, ""))
        if x_value is None or y_value is None:
            continue
        valid_count += 1
        if len(points) < _MAX_SCATTER_POINTS:
            points.append({"x": x_value, "y": y_value})
    if not points:
        raise ManualVisualizationPreviewError("The selected fields have no numeric pairs to plot.")
    return {
        "chart_type": "scatter",
        "x_label": x_profile.name,
        "y_label": y_profile.name,
        "aggregation": "Row values",
        "record_count": valid_count,
        "truncated": valid_count > len(points),
        "points": points,
    }


def _bubble_preview(
    rows: tuple[Any, ...],
    *,
    x_profile: ColumnProfile | None,
    y_profile: ColumnProfile | None,
    size_profile: ColumnProfile | None,
) -> dict[str, Any]:
    if x_profile is None or y_profile is None or size_profile is None:
        raise ManualVisualizationPreviewError(
            "A bubble chart needs numeric X-axis, Y-axis, and Size fields."
        )
    if not all(_is_numeric(column) for column in (x_profile, y_profile, size_profile)):
        raise ManualVisualizationPreviewError(
            "A bubble chart supports numeric X-axis, Y-axis, and Size fields only."
        )
    points: list[dict[str, float]] = []
    valid_count = 0
    for row in rows:
        x_value = _number(row.values.get(x_profile.name, ""))
        y_value = _number(row.values.get(y_profile.name, ""))
        size_value = _number(row.values.get(size_profile.name, ""))
        if x_value is None or y_value is None or size_value is None or size_value <= 0:
            continue
        valid_count += 1
        if len(points) < _MAX_SCATTER_POINTS:
            points.append({"x": x_value, "y": y_value, "size": size_value})
    if not points:
        raise ManualVisualizationPreviewError(
            "The selected fields have no positive numeric bubble sizes to plot."
        )
    return {
        "chart_type": "bubble",
        "x_label": x_profile.name,
        "y_label": y_profile.name,
        "size_label": size_profile.name,
        "aggregation": "Row values",
        "record_count": valid_count,
        "truncated": valid_count > len(points),
        "points": points,
    }


def _combo_preview(
    rows: tuple[Any, ...],
    *,
    x_profile: ColumnProfile | None,
    y_profile: ColumnProfile | None,
    secondary_y_profile: ColumnProfile | None,
) -> dict[str, Any]:
    if x_profile is None or y_profile is None or secondary_y_profile is None:
        raise ManualVisualizationPreviewError(
            "A combo chart needs X-axis, Y-axis, and Secondary Y fields."
        )
    if not _is_numeric(y_profile) or not _is_numeric(secondary_y_profile):
        raise ManualVisualizationPreviewError(
            "A combo chart needs numeric Y-axis and Secondary Y fields."
        )
    if y_profile.name == secondary_y_profile.name:
        raise ManualVisualizationPreviewError(
            "Choose different fields for Y-axis and Secondary Y."
        )
    grouped: dict[str, tuple[list[float], list[float]]] = {}
    record_count = 0
    for row in rows:
        label = row.values.get(x_profile.name, "").strip()
        y_value = _number(row.values.get(y_profile.name, ""))
        secondary_value = _number(row.values.get(secondary_y_profile.name, ""))
        if _is_missing(label) or y_value is None or secondary_value is None:
            continue
        primary_values, secondary_values = grouped.setdefault(label, ([], []))
        primary_values.append(y_value)
        secondary_values.append(secondary_value)
        record_count += 1
    if not grouped:
        raise ManualVisualizationPreviewError(
            "The selected combo fields have no values to plot."
        )
    points = [
        {
            "x": label,
            "y": math.fsum(primary_values),
            "secondary_y": math.fsum(secondary_values),
        }
        for label, (primary_values, secondary_values) in grouped.items()
    ]
    if x_profile.inferred_type is ColumnType.DATETIME:
        points.sort(key=lambda point: str(point["x"]))
    else:
        points.sort(key=lambda point: abs(float(point["y"])), reverse=True)
    truncated = len(points) > _MAX_GROUPS
    return {
        "chart_type": "combo",
        "x_label": x_profile.name,
        "y_label": y_profile.name,
        "secondary_y_label": secondary_y_profile.name,
        "aggregation": f"Sum of {y_profile.name} and {secondary_y_profile.name}",
        "record_count": record_count,
        "truncated": truncated,
        "points": points[:_MAX_GROUPS],
    }


def _target_preview(
    rows: tuple[Any, ...],
    *,
    measure: ColumnProfile | None,
    target_value: str | None,
    chart_type: str,
) -> dict[str, Any]:
    if measure is None or not _is_numeric(measure):
        raise ManualVisualizationPreviewError(
            "Gauge and bullet charts need a numeric field."
        )
    target = _number(target_value or "")
    if target is None or target <= 0:
        raise ManualVisualizationPreviewError(
            "Enter a positive target value for this visualization."
        )
    values = [
        value
        for row in rows
        if (value := _number(row.values.get(measure.name, ""))) is not None
    ]
    if not values:
        raise ManualVisualizationPreviewError(
            "The selected field has no numeric values to summarize."
        )
    actual = math.fsum(values)
    return {
        "chart_type": chart_type,
        "x_label": "",
        "y_label": measure.name,
        "target": target,
        "aggregation": f"Sum of {measure.name}",
        "record_count": len(values),
        "truncated": False,
        "points": [{"x": measure.name, "y": actual}],
    }


def _histogram_preview(
    rows: tuple[Any, ...],
    *,
    measure: ColumnProfile | None,
) -> dict[str, Any]:
    if measure is None or not _is_numeric(measure):
        raise ManualVisualizationPreviewError("A histogram needs one numeric field.")
    values = [
        value
        for row in rows
        if (value := _number(row.values.get(measure.name, ""))) is not None
    ]
    if not values:
        raise ManualVisualizationPreviewError("The selected field has no numeric values to plot.")
    minimum = min(values)
    maximum = max(values)
    if minimum == maximum:
        points = [{"x": _format_range(minimum, maximum), "y": len(values)}]
    else:
        width = (maximum - minimum) / _HISTOGRAM_BINS
        counts = [0] * _HISTOGRAM_BINS
        for value in values:
            index = min(int((value - minimum) / width), _HISTOGRAM_BINS - 1)
            counts[index] += 1
        points = [
            {
                "x": _format_range(minimum + index * width, minimum + (index + 1) * width),
                "y": count,
            }
            for index, count in enumerate(counts)
        ]
    return {
        "chart_type": "histogram",
        "x_label": measure.name,
        "y_label": "Record count",
        "aggregation": "Binned values",
        "record_count": len(values),
        "truncated": False,
        "points": points,
    }


def _box_preview(
    rows: tuple[Any, ...],
    *,
    x_profile: ColumnProfile | None,
    y_profile: ColumnProfile | None,
) -> dict[str, Any]:
    measure = y_profile
    group = x_profile
    if measure is None and x_profile is not None and _is_numeric(x_profile):
        measure = x_profile
        group = None
    if measure is None or not _is_numeric(measure):
        raise ManualVisualizationPreviewError("A box plot needs a numeric Y-axis field.")
    if group is not None and _is_numeric(group):
        raise ManualVisualizationPreviewError(
            "A grouped box plot needs a categorical X-axis field."
        )

    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        label = "All records" if group is None else row.values.get(group.name, "").strip()
        value = _number(row.values.get(measure.name, ""))
        if _is_missing(label) or value is None:
            continue
        grouped[label].append(value)
    if not grouped:
        raise ManualVisualizationPreviewError(
            "The selected fields have no numeric values to summarize."
        )
    ordered = sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True)
    truncated = len(ordered) > _MAX_GROUPS
    points = []
    for label, values in ordered[:_MAX_GROUPS]:
        sorted_values = sorted(values)
        points.append(
            {
                "x": label,
                "minimum": sorted_values[0],
                "q1": _percentile(sorted_values, 0.25),
                "median": _percentile(sorted_values, 0.5),
                "q3": _percentile(sorted_values, 0.75),
                "maximum": sorted_values[-1],
                "count": len(sorted_values),
            }
        )
    return {
        "chart_type": "box",
        "x_label": group.name if group is not None else "Distribution",
        "y_label": measure.name,
        "aggregation": f"Distribution of {measure.name}",
        "record_count": sum(len(values) for values in grouped.values()),
        "truncated": truncated,
        "points": points,
    }


def _stacked_preview(
    rows: tuple[Any, ...],
    *,
    x_profile: ColumnProfile | None,
    y_profile: ColumnProfile | None,
    series_profile: ColumnProfile | None,
    chart_type: str,
) -> dict[str, Any]:
    chart_label = {
        "heatmap": "heatmap",
        "grouped_column": "grouped column chart",
        "multi_line": "multi-series line chart",
        "stacked_100_column": "100% stacked column chart",
        "stacked_100_bar": "100% stacked bar chart",
    }.get(chart_type, "stacked chart")
    if x_profile is None or y_profile is None or series_profile is None:
        raise ManualVisualizationPreviewError(
            f"A {chart_label} needs X-axis, Y-axis, and Legend fields."
        )
    if not _is_numeric(y_profile):
        raise ManualVisualizationPreviewError(
            f"A {chart_label} needs a numeric Y-axis field."
        )
    if _is_numeric(series_profile):
        raise ManualVisualizationPreviewError(
            f"A {chart_label} needs a categorical Legend field."
        )
    if len({x_profile.name, y_profile.name, series_profile.name}) != 3:
        raise ManualVisualizationPreviewError(
            "Use different fields for X-axis, Y-axis, and Legend."
        )

    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    record_count = 0
    for row in rows:
        x_label = row.values.get(x_profile.name, "").strip()
        series_label = row.values.get(series_profile.name, "").strip()
        value = _number(row.values.get(y_profile.name, ""))
        if _is_missing(x_label) or _is_missing(series_label) or value is None:
            continue
        grouped[(x_label, series_label)].append(value)
        record_count += 1
    if not grouped:
        raise ManualVisualizationPreviewError(
            "The selected fields have no values to plot."
        )

    aggregated = {
        key: math.fsum(values)
        for key, values in grouped.items()
    }
    normalized_stacks = {"stacked_100_column", "stacked_100_bar"}
    stacked_charts = {"stacked_column", "stacked_bar"} | normalized_stacks
    if chart_type in stacked_charts and any(
        value < 0 for value in aggregated.values()
    ):
        raise ManualVisualizationPreviewError(
            "Stacked previews currently require non-negative aggregated values."
        )
    if chart_type in stacked_charts and not any(
        value > 0 for value in aggregated.values()
    ):
        raise ManualVisualizationPreviewError(
            "A stacked preview needs at least one positive aggregated value."
        )

    x_totals: dict[str, float] = defaultdict(float)
    series_totals: dict[str, float] = defaultdict(float)
    for (x_label, series_label), value in aggregated.items():
        x_totals[x_label] += value
        series_totals[series_label] += value
    if x_profile.inferred_type is ColumnType.DATETIME:
        x_values = sorted(x_totals)[:_MAX_STACK_GROUPS]
    else:
        x_values = [
            label
            for label, _value in sorted(
                x_totals.items(), key=lambda item: item[1], reverse=True
            )[:_MAX_STACK_GROUPS]
        ]
    series_values = [
        label
        for label, _value in sorted(
            series_totals.items(), key=lambda item: item[1], reverse=True
        )[:_MAX_STACK_SERIES]
    ]
    points = [
        {"x": x_label, "series": series_label, "y": aggregated.get((x_label, series_label), 0.0)}
        for x_label in x_values
        for series_label in series_values
    ]
    return {
        "chart_type": chart_type,
        "x_label": x_profile.name,
        "y_label": y_profile.name,
        "series_label": series_profile.name,
        "aggregation": f"Sum of {y_profile.name}",
        "record_count": record_count,
        "truncated": (
            len(x_totals) > len(x_values)
            or len(series_totals) > len(series_values)
        ),
        "points": points,
    }


def _grouped_preview(
    rows: tuple[Any, ...],
    *,
    x_profile: ColumnProfile | None,
    y_profile: ColumnProfile | None,
    chart_type: str,
) -> dict[str, Any]:
    if chart_type not in {
        "column",
        "bar",
        "line",
        "area",
        "pie",
        "donut",
        "table",
        "pareto",
        "waterfall",
        "funnel",
        "treemap",
        "radar",
    }:
        raise ManualVisualizationPreviewError("That grouped visualization is unsupported.")
    if x_profile is None:
        raise ManualVisualizationPreviewError("This visualization needs an X-axis field.")
    if y_profile is not None and not _is_numeric(y_profile):
        raise ManualVisualizationPreviewError("The Y-axis needs a numeric field.")
    if chart_type in {"line", "area"} and x_profile.inferred_type is not ColumnType.DATETIME:
        raise ManualVisualizationPreviewError("Line and area charts need a date/time X-axis.")

    grouped: dict[str, list[float]] = defaultdict(list)
    record_count = 0
    for row in rows:
        label = row.values.get(x_profile.name, "").strip()
        if _is_missing(label):
            continue
        if y_profile is None:
            grouped[label].append(1.0)
        else:
            value = _number(row.values.get(y_profile.name, ""))
            if value is None:
                continue
            grouped[label].append(value)
        record_count += 1
    if not grouped:
        raise ManualVisualizationPreviewError("The selected fields have no values to plot.")

    points = [
        {"x": label, "y": math.fsum(values) if y_profile is not None else len(values)}
        for label, values in grouped.items()
    ]
    if chart_type in {"line", "area"}:
        points.sort(key=lambda point: str(point["x"]))
    elif chart_type != "waterfall":
        points.sort(key=lambda point: abs(float(point["y"])), reverse=True)
    limit = (
        _MAX_DONUT_GROUPS
        if chart_type in {"pie", "donut", "funnel", "treemap", "radar"}
        else _MAX_GROUPS
    )
    truncated = len(points) > limit
    points = points[:limit]
    if chart_type in {
        "pie",
        "donut",
        "pareto",
        "funnel",
        "treemap",
        "radar",
    }:
        points = [point for point in points if float(point["y"]) > 0]
        if not points:
            raise ManualVisualizationPreviewError(
                "This visualization needs positive aggregated values."
            )

    return {
        "chart_type": chart_type,
        "x_label": x_profile.name,
        "y_label": y_profile.name if y_profile is not None else "Record count",
        "aggregation": f"Sum of {y_profile.name}" if y_profile is not None else "Record count",
        "record_count": record_count,
        "truncated": truncated,
        "points": points,
    }


def _is_numeric(column: ColumnProfile) -> bool:
    return column.inferred_type is ColumnType.NUMERIC


def _number(value: str) -> float | None:
    if _is_missing(value):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _is_missing(value: str) -> bool:
    return value.strip().casefold() in _MISSING_MARKERS


def _format_range(start: float, end: float) -> str:
    return f"{start:.4g}–{end:.4g}"


def _percentile(sorted_values: list[float], proportion: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * proportion
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
