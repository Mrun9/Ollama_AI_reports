"""Validated, reproducible manual visualizations for configured and supplementary data."""

from __future__ import annotations

import json
import math
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from insight_reporter.business_config import (
    BusinessConfiguration,
    MetricConfiguration,
)
from insight_reporter.dataset_profile import ColumnType, DatasetProfile
from insight_reporter.dataset_view import DatasetRow, DatasetView
from insight_reporter.derived_metrics import (
    aggregate_derived_metric,
    evaluate_derived_metric,
)
from insight_reporter.formula_engine import aggregate_row_values

CHART_TYPES = (
    "time_line",
    "category_bar",
    "category_bar_horizontal",
    "scatter",
    "histogram",
    "box",
)
AGGREGATIONS = ("configured", "sum", "mean", "median", "min", "max")
DATE_GRANULARITIES = ("day", "month", "quarter", "year")
SCALES = ("linear", "log")
SORT_OPTIONS = ("label", "value")
SORT_DIRECTIONS = ("ascending", "descending")
FILTER_MODES = ("include", "exclude")
_MISSING_MARKERS = frozenset({"", "na", "n/a", "null", "none", "nan"})
_DATASET_ID = re.compile(r"[0-9a-f]{32}")
_VISUALIZATION_ID = re.compile(r"VIS-[0-9A-F]{16}")
_PREVIEW_TOKEN = re.compile(r"[0-9a-f]{32}")
_CHART_FILENAME = re.compile(r"[0-9a-f]{32}\.png")
_MAX_TITLE_CHARACTERS = 120
_MAX_PURPOSE_CHARACTERS = 500
_MAX_LABEL_CHARACTERS = 60
_MAX_MEASURES = 5
_MAX_TOP_N = 50
_MAX_FILTER_VALUES = 50
_MAX_BINS = 50
_MAX_SERIES = 12
_PREVIEW_RETENTION_SECONDS = 24 * 60 * 60


class VisualizationError(ValueError):
    """Raised when a manual visualization is invalid or cannot be reproduced."""


@dataclass(frozen=True)
class VisualizationSpec:
    """User selections after shape validation but before dataset validation."""

    title: str
    purpose: str
    chart_type: str
    measure_selectors: tuple[str, ...]
    x_column: str | None
    series_column: str | None
    aggregation: str
    date_granularity: str
    filter_column: str | None
    filter_mode: str
    filter_values: tuple[str, ...]
    date_start: str | None
    date_end: str | None
    sort_by: str
    sort_direction: str
    top_n: int
    scale: str
    bin_count: int
    include_in_report: bool
    replaces_visualization_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "purpose": self.purpose,
            "chart_type": self.chart_type,
            "measure_selectors": list(self.measure_selectors),
            "x_column": self.x_column,
            "series_column": self.series_column,
            "aggregation": self.aggregation,
            "date_granularity": self.date_granularity,
            "filter_column": self.filter_column,
            "filter_mode": self.filter_mode,
            "filter_values": list(self.filter_values),
            "date_start": self.date_start,
            "date_end": self.date_end,
            "sort_by": self.sort_by,
            "sort_direction": self.sort_direction,
            "top_n": self.top_n,
            "scale": self.scale,
            "bin_count": self.bin_count,
            "include_in_report": self.include_in_report,
            "replaces_visualization_id": self.replaces_visualization_id,
        }


@dataclass(frozen=True)
class VisualizationMeasure:
    selector: str
    label: str
    role: str
    display_format: str
    effective_aggregation: str
    source_columns: tuple[str, ...]
    formula: str | None
    calculation_level: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "selector": self.selector,
            "label": self.label,
            "role": self.role,
            "display_format": self.display_format,
            "effective_aggregation": self.effective_aggregation,
            "source_columns": list(self.source_columns),
            "formula": self.formula,
            "calculation_level": self.calculation_level,
        }


@dataclass(frozen=True)
class ManualChart:
    filename: str
    title: str
    alt_text: str
    chart_type: str
    record_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "title": self.title,
            "alt_text": self.alt_text,
            "chart_type": self.chart_type,
            "record_count": self.record_count,
        }


@dataclass(frozen=True)
class VisualizationArtifact:
    schema_version: int
    visualization_id: str | None
    dataset_id: str
    classification: str
    source: dict[str, object]
    spec: VisualizationSpec
    measures: tuple[VisualizationMeasure, ...]
    supporting_data: tuple[dict[str, object], ...]
    source_columns: tuple[str, ...]
    filtered_record_count: int
    chart: ManualChart
    created_at: str
    assistant: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "visualization_id": self.visualization_id,
            "dataset_id": self.dataset_id,
            "classification": self.classification,
            "source": self.source,
            "spec": self.spec.to_dict(),
            "measures": [measure.to_dict() for measure in self.measures],
            "supporting_data": list(self.supporting_data),
            "source_columns": list(self.source_columns),
            "filtered_record_count": self.filtered_record_count,
            "chart": self.chart.to_dict(),
            "created_at": self.created_at,
            "assistant": self.assistant,
        }


@dataclass(frozen=True)
class _ResolvedMeasure:
    public: VisualizationMeasure
    metric: MetricConfiguration | None = None
    column: str | None = None
    count_records: bool = False

    @property
    def is_row_level(self) -> bool:
        if self.count_records:
            return False
        if self.column is not None:
            return True
        if self.metric is None:
            return False
        return (
            self.metric.metric_type == "source"
            or (
                self.metric.derived_metric is not None
                and self.metric.derived_metric.calculation_level == "row"
            )
        )


def parse_visualization_spec(values: dict[str, object]) -> VisualizationSpec:
    """Parse untrusted form-shaped values into a bounded visualization request."""

    title = _required_text(values.get("title"), "Chart title", _MAX_TITLE_CHARACTERS)
    purpose = _bounded_optional_text(
        values.get("purpose"),
        "Visualization purpose",
        _MAX_PURPOSE_CHARACTERS,
    )
    chart_type = _choice(values.get("chart_type"), CHART_TYPES, "chart type")
    raw_measures = values.get("measure_selectors")
    if not isinstance(raw_measures, (list, tuple)):
        raw_measures = []
    measure_selectors = tuple(
        value.strip()
        for value in raw_measures
        if isinstance(value, str) and value.strip()
    )
    if not 1 <= len(measure_selectors) <= _MAX_MEASURES:
        raise VisualizationError(
            f"Select between 1 and {_MAX_MEASURES} visualization measures."
        )
    if len(measure_selectors) != len(set(measure_selectors)):
        raise VisualizationError("Visualization measures must not contain duplicates.")

    raw_filter_values = values.get("filter_values")
    if isinstance(raw_filter_values, str):
        filter_values = tuple(
            line.strip()
            for line in raw_filter_values.splitlines()
            if line.strip()
        )
    elif isinstance(raw_filter_values, (list, tuple)):
        filter_values = tuple(
            value.strip()
            for value in raw_filter_values
            if isinstance(value, str) and value.strip()
        )
    else:
        filter_values = ()
    if len(filter_values) > _MAX_FILTER_VALUES:
        raise VisualizationError(
            f"Select at most {_MAX_FILTER_VALUES} filter values."
        )
    if len(filter_values) != len(set(filter_values)):
        raise VisualizationError("Filter values must not contain duplicates.")

    top_n = _bounded_integer(values.get("top_n"), "Top-N", 1, _MAX_TOP_N, 10)
    bin_count = _bounded_integer(values.get("bin_count"), "Bin count", 5, _MAX_BINS, 10)
    replacement = _optional_text(values.get("replaces_visualization_id"))
    if replacement is not None and _VISUALIZATION_ID.fullmatch(replacement) is None:
        raise VisualizationError("Replacement visualization ID is invalid.")

    return VisualizationSpec(
        title=title,
        purpose=purpose,
        chart_type=chart_type,
        measure_selectors=measure_selectors,
        x_column=_optional_text(values.get("x_column")),
        series_column=_optional_text(values.get("series_column")),
        aggregation=_choice(values.get("aggregation"), AGGREGATIONS, "aggregation"),
        date_granularity=_choice(
            values.get("date_granularity"),
            DATE_GRANULARITIES,
            "date granularity",
        ),
        filter_column=_optional_text(values.get("filter_column")),
        filter_mode=_choice(values.get("filter_mode"), FILTER_MODES, "filter mode"),
        filter_values=filter_values,
        date_start=_optional_date(values.get("date_start"), "start date"),
        date_end=_optional_date(values.get("date_end"), "end date"),
        sort_by=_choice(values.get("sort_by"), SORT_OPTIONS, "sort field"),
        sort_direction=_choice(
            values.get("sort_direction"),
            SORT_DIRECTIONS,
            "sort direction",
        ),
        top_n=top_n,
        scale=_choice(values.get("scale"), SCALES, "axis scale"),
        bin_count=bin_count,
        include_in_report=_boolean(values.get("include_in_report")),
        replaces_visualization_id=replacement,
    )


def build_visualization(
    view: DatasetView,
    *,
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
    spec: VisualizationSpec,
    chart_dir: Path,
    assistant_metadata: dict[str, object] | None = None,
) -> VisualizationArtifact:
    """Validate a visualization, calculate its data, and render a draft chart."""

    _validate_dataset(view, profile, configuration)
    assistant = _validate_assistant_metadata(assistant_metadata)
    measures = tuple(
        _resolve_measure(selector, profile, configuration, spec)
        for selector in spec.measure_selectors
    )
    _validate_compatibility(spec, measures, profile, configuration)
    rows = _filter_rows(view.iter_rows(), spec, profile, configuration)
    if not rows:
        raise VisualizationError("The selected filters produce no source records.")
    if spec.series_column is not None:
        series_values = {
            row.values[spec.series_column].strip()
            for row in rows
            if not _is_missing(row.values[spec.series_column])
        }
        if len(series_values) > _MAX_SERIES:
            raise VisualizationError(
                f"Series grouping supports at most {_MAX_SERIES} displayed values."
            )

    supporting_data = _prepare_supporting_data(
        rows,
        spec=spec,
        measures=measures,
    )
    if not supporting_data:
        raise VisualizationError(
            "The selected chart has no finite values after validation and filtering."
        )
    plotted_values = _plotted_values(spec.chart_type, supporting_data)
    if spec.scale == "log" and (
        not plotted_values or any(value <= 0 for value in plotted_values)
    ):
        raise VisualizationError(
            "Logarithmic scale requires every plotted value to be greater than zero."
        )

    chart_dir = _secure_directory(chart_dir)
    chart = _render_chart(
        spec,
        measures=measures,
        supporting_data=supporting_data,
        chart_dir=chart_dir,
    )
    source_columns = _source_columns(spec, measures, configuration)
    classification = (
        "kpi"
        if all(measure.public.role == "configured_kpi" for measure in measures)
        else "supplementary"
    )
    return VisualizationArtifact(
        schema_version=3,
        visualization_id=None,
        dataset_id=configuration.dataset_id,
        classification=classification,
        source=_source_metadata(view),
        spec=spec,
        measures=tuple(measure.public for measure in measures),
        supporting_data=supporting_data,
        source_columns=source_columns,
        filtered_record_count=len(rows),
        chart=chart,
        created_at=datetime.now(UTC).isoformat(),
        assistant=assistant,
    )


def save_preview(
    artifact: VisualizationArtifact,
    *,
    preview_dir: Path,
    chart_dir: Path,
) -> str:
    """Persist a short-lived draft artifact for a POST/Redirect/GET preview."""

    preview_dir = _secure_directory(preview_dir)
    _delete_expired_previews(preview_dir, chart_dir=chart_dir)
    token = secrets.token_hex(16)
    try:
        _atomic_json_write(preview_dir / f"{token}.json", artifact.to_dict())
    except VisualizationError:
        delete_chart(artifact.chart.filename, chart_dir=chart_dir)
        raise
    return token


def load_preview(
    token: str,
    *,
    dataset_id: str,
    preview_dir: Path,
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
) -> VisualizationArtifact:
    """Load and fully revalidate one unexpired draft."""

    if _PREVIEW_TOKEN.fullmatch(token) is None:
        raise VisualizationError("Visualization preview token is invalid.")
    path = preview_dir / f"{token}.json"
    try:
        if time.time() - path.stat().st_mtime > _PREVIEW_RETENTION_SECONDS:
            raise VisualizationError("Visualization preview has expired.")
    except OSError as error:
        raise VisualizationError("Visualization preview is unavailable.") from error
    return _load_artifact(
        path,
        dataset_id=dataset_id,
        profile=profile,
        configuration=configuration,
        allow_draft=True,
    )


def save_visualization(
    artifact: VisualizationArtifact,
    *,
    visualization_dir: Path,
    previous: VisualizationArtifact | None = None,
) -> tuple[VisualizationArtifact, Path]:
    """Atomically save a new or replacement visualization configuration."""

    dataset_dir = _dataset_visualization_dir(
        visualization_dir,
        artifact.dataset_id,
        create=True,
    )
    replacement_id = artifact.spec.replaces_visualization_id
    if previous is not None:
        if replacement_id != previous.visualization_id:
            raise VisualizationError("Replacement visualization does not match the draft.")
        visualization_id = previous.visualization_id
    elif replacement_id is not None:
        raise VisualizationError("Replacement visualization is unavailable.")
    else:
        visualization_id = f"VIS-{secrets.token_hex(8).upper()}"
    assert visualization_id is not None
    saved = replace(artifact, visualization_id=visualization_id)
    path = dataset_dir / f"{visualization_id}.json"
    _atomic_json_write(path, saved.to_dict())
    return saved, path


def load_visualization(
    visualization_id: str,
    *,
    dataset_id: str,
    visualization_dir: Path,
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
) -> VisualizationArtifact:
    """Load and revalidate one saved visualization."""

    if _VISUALIZATION_ID.fullmatch(visualization_id) is None:
        raise VisualizationError("Visualization ID is invalid.")
    directory = _dataset_visualization_dir(
        visualization_dir,
        dataset_id,
        create=False,
    )
    return _load_artifact(
        directory / f"{visualization_id}.json",
        dataset_id=dataset_id,
        profile=profile,
        configuration=configuration,
        allow_draft=False,
    )


def list_visualizations(
    *,
    dataset_id: str,
    visualization_dir: Path,
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
) -> tuple[VisualizationArtifact, ...]:
    """Load every valid saved visualization for one dataset."""

    try:
        directory = _dataset_visualization_dir(
            visualization_dir,
            dataset_id,
            create=False,
        )
    except VisualizationError:
        return ()
    artifacts: list[VisualizationArtifact] = []
    for path in sorted(directory.glob("VIS-*.json")):
        try:
            artifact = _load_artifact(
                path,
                dataset_id=dataset_id,
                profile=profile,
                configuration=configuration,
                allow_draft=False,
            )
        except VisualizationError:
            continue
        artifacts.append(artifact)
    return tuple(artifacts)


def delete_preview(token: str, *, preview_dir: Path) -> None:
    if _PREVIEW_TOKEN.fullmatch(token) is not None:
        (preview_dir / f"{token}.json").unlink(missing_ok=True)


def delete_chart(filename: str, *, chart_dir: Path) -> None:
    """Delete one exact randomized chart basename."""

    if _CHART_FILENAME.fullmatch(filename) is None:
        return
    directory = _secure_directory(chart_dir)
    path = (directory / filename).resolve()
    if path.parent == directory:
        path.unlink(missing_ok=True)


def artifact_chart_filename(artifact: VisualizationArtifact) -> str:
    if _CHART_FILENAME.fullmatch(artifact.chart.filename) is None:
        raise VisualizationError("Visualization chart filename is invalid.")
    return artifact.chart.filename


def spec_to_form(artifact: VisualizationArtifact) -> dict[str, object]:
    """Return form-shaped values for editing a saved visualization."""

    spec = artifact.spec.to_dict()
    spec["filter_values"] = "\n".join(artifact.spec.filter_values)
    spec["include_in_report"] = "yes" if artifact.spec.include_in_report else ""
    spec["replaces_visualization_id"] = artifact.visualization_id
    return spec


def _resolve_measure(
    selector: str,
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
    spec: VisualizationSpec,
) -> _ResolvedMeasure:
    if selector == "count:records":
        return _ResolvedMeasure(
            public=VisualizationMeasure(
                selector=selector,
                label="Record count",
                role="supplementary_record_count",
                display_format="number",
                effective_aggregation="count",
                source_columns=(),
                formula=None,
                calculation_level="aggregate",
            ),
            count_records=True,
        )
    if selector.startswith("column:"):
        column_name = selector.removeprefix("column:")
        column = profile.column(column_name)
        if (
            column is None
            or column.inferred_type is not ColumnType.NUMERIC
            or column.is_constant
            or column.is_empty
        ):
            raise VisualizationError(
                "Supplementary measures must be non-constant numeric source columns."
            )
        if spec.chart_type in {"scatter", "histogram", "box"}:
            aggregation = "row_value"
        else:
            aggregation = "mean" if spec.aggregation == "configured" else spec.aggregation
        return _ResolvedMeasure(
            public=VisualizationMeasure(
                selector=selector,
                label=column_name,
                role="supplementary_numeric_column",
                display_format="number",
                effective_aggregation=aggregation,
                source_columns=(column_name,),
                formula=None,
                calculation_level="row",
            ),
            column=column_name,
        )
    if selector.startswith("metric:"):
        metric_id = selector.removeprefix("metric:")
        metric = next(
            (item for item in configuration.metrics if item.metric_id == metric_id),
            None,
        )
        if metric is None:
            raise VisualizationError("Selected KPI is not configured.")
        formula = (
            metric.derived_metric.formula_label
            if metric.derived_metric is not None
            else None
        )
        level = (
            metric.derived_metric.calculation_level
            if metric.derived_metric is not None
            else "row"
        )
        if metric.derived_metric is not None and level == "aggregate":
            aggregation = "formula"
        elif spec.chart_type in {"scatter", "histogram", "box"}:
            aggregation = "row_value"
        elif spec.aggregation != "configured":
            aggregation = spec.aggregation
        elif metric.derived_metric is not None:
            aggregation = metric.derived_metric.aggregation
        else:
            aggregation = "sum"
        return _ResolvedMeasure(
            public=VisualizationMeasure(
                selector=selector,
                label=metric.name,
                role="configured_kpi",
                display_format=metric.display_format,
                effective_aggregation=aggregation,
                source_columns=metric.source_columns,
                formula=formula,
                calculation_level=level,
            ),
            metric=metric,
        )
    raise VisualizationError("Visualization measure selector is invalid.")


def _validate_compatibility(
    spec: VisualizationSpec,
    measures: tuple[_ResolvedMeasure, ...],
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
) -> None:
    chart_type = spec.chart_type
    if chart_type == "time_line":
        if spec.x_column not in profile.date_candidates:
            raise VisualizationError("Time-series charts require a valid date column.")
    elif chart_type in {"category_bar", "category_bar_horizontal"}:
        if spec.x_column not in profile.category_candidates:
            raise VisualizationError(
                "Category charts require a valid category column."
            )
    elif chart_type == "scatter":
        if len(measures) != 1 or not measures[0].is_row_level:
            raise VisualizationError(
                "Scatter plots require exactly one row-level KPI or numeric measure."
            )
        x_profile = profile.column(spec.x_column or "")
        if (
            x_profile is None
            or x_profile.inferred_type is not ColumnType.NUMERIC
            or x_profile.is_constant
        ):
            raise VisualizationError(
                "Scatter plots require a non-constant numeric x-axis column."
            )
    elif chart_type in {"histogram", "box"}:
        if len(measures) != 1 or not measures[0].is_row_level:
            raise VisualizationError(
                "Distribution charts require exactly one row-level KPI or numeric measure."
            )
        if chart_type == "box" and (
            spec.x_column is not None
            and spec.x_column not in profile.category_candidates
        ):
            raise VisualizationError(
                "A grouped box plot requires a valid category column."
            )

    if spec.series_column is not None:
        if spec.series_column not in profile.category_candidates:
            raise VisualizationError("Series must use a valid category column.")
        if chart_type not in {"time_line", "scatter"}:
            raise VisualizationError(
                "Series grouping is supported only for line and scatter charts."
            )
        if chart_type == "time_line" and len(measures) != 1:
            raise VisualizationError(
                "A categorized time-series chart supports exactly one measure."
            )
    if len(measures) > 1:
        formats = {measure.public.display_format for measure in measures}
        if len(formats) != 1:
            raise VisualizationError(
                "Multiple measures must use the same display format and compatible units."
            )
    if any(measure.count_records for measure in measures) and chart_type not in {
        "time_line",
        "category_bar",
        "category_bar_horizontal",
    }:
        raise VisualizationError(
            "Record count is supported only for time or category charts."
        )
    if spec.scale == "log" and chart_type in {"histogram", "box"}:
        raise VisualizationError(
            "Logarithmic scale is supported for line, bar, and scatter charts."
        )
    if spec.date_start is not None or spec.date_end is not None:
        date_column = configuration.date_column
        if chart_type == "time_line":
            date_column = spec.x_column
        if date_column is None:
            raise VisualizationError(
                "Date filters require a configured or selected date column."
            )
        if (
            spec.date_start is not None
            and spec.date_end is not None
            and spec.date_start > spec.date_end
        ):
            raise VisualizationError("Start date must not be after end date.")
    if spec.filter_column is None and spec.filter_values:
        raise VisualizationError("Filter values require a category filter column.")
    if spec.filter_column is not None:
        if spec.filter_column not in profile.category_candidates:
            raise VisualizationError("Filters require a valid category column.")


def _filter_rows(
    rows: tuple[DatasetRow, ...],
    spec: VisualizationSpec,
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
) -> tuple[DatasetRow, ...]:
    actual_filter_values: set[str] = set()
    if spec.filter_column is not None:
        actual_filter_values = {
            row.values[spec.filter_column].strip()
            for row in rows
            if not _is_missing(row.values[spec.filter_column])
        }
        if not set(spec.filter_values).issubset(actual_filter_values):
            raise VisualizationError(
                "One or more category filter values do not exist in the dataset."
            )
    date_column = spec.x_column if spec.chart_type == "time_line" else configuration.date_column
    filtered: list[DatasetRow] = []
    for row in rows:
        if spec.filter_column is not None and spec.filter_values:
            selected = row.values[spec.filter_column].strip() in spec.filter_values
            if (spec.filter_mode == "include" and not selected) or (
                spec.filter_mode == "exclude" and selected
            ):
                continue
        if (spec.date_start is not None or spec.date_end is not None) and date_column:
            parsed = _parse_datetime(row.values[date_column])
            if parsed is None:
                continue
            label = parsed.date().isoformat()
            if spec.date_start is not None and label < spec.date_start:
                continue
            if spec.date_end is not None and label > spec.date_end:
                continue
        filtered.append(row)
    return tuple(filtered)


def _prepare_supporting_data(
    rows: tuple[DatasetRow, ...],
    *,
    spec: VisualizationSpec,
    measures: tuple[_ResolvedMeasure, ...],
) -> tuple[dict[str, object], ...]:
    if spec.chart_type in {"time_line", "category_bar", "category_bar_horizontal"}:
        return _grouped_data(rows, spec=spec, measures=measures)
    measure = measures[0]
    if spec.chart_type == "scatter":
        output: list[dict[str, object]] = []
        for row in rows:
            x_value = _number(row.values[spec.x_column or ""])
            y_value = _row_measure_value(row, measure)
            if x_value is None or y_value is None:
                continue
            output.append(
                {
                    "row_number": row.number,
                    "x": x_value,
                    "y": y_value,
                    "series": (
                        row.values[spec.series_column].strip()
                        if spec.series_column is not None
                        else None
                    ),
                }
            )
        return tuple(output)
    output = []
    for row in rows:
        value = _row_measure_value(row, measure)
        if value is None:
            continue
        output.append(
            {
                "row_number": row.number,
                "value": value,
                "category": (
                    row.values[spec.x_column].strip()
                    if spec.chart_type == "box" and spec.x_column is not None
                    else None
                ),
            }
        )
    return tuple(output)


def _grouped_data(
    rows: tuple[DatasetRow, ...],
    *,
    spec: VisualizationSpec,
    measures: tuple[_ResolvedMeasure, ...],
) -> tuple[dict[str, object], ...]:
    groups: dict[tuple[str, str | None], list[DatasetRow]] = {}
    for row in rows:
        if spec.chart_type == "time_line":
            parsed = _parse_datetime(row.values[spec.x_column or ""])
            if parsed is None:
                continue
            x_value = _period_label(parsed, spec.date_granularity)
        else:
            x_value = row.values[spec.x_column or ""].strip()
            if _is_missing(x_value):
                continue
        series = (
            row.values[spec.series_column].strip()
            if spec.series_column is not None
            else None
        )
        if series is not None and _is_missing(series):
            continue
        groups.setdefault((x_value, series), []).append(row)

    output: list[dict[str, object]] = []
    for (x_value, series), grouped_rows in groups.items():
        for measure in measures:
            value = _group_measure_value(tuple(grouped_rows), measure)
            if value is None:
                continue
            output.append(
                {
                    "x": x_value,
                    "series": series,
                    "measure_selector": measure.public.selector,
                    "measure": measure.public.label,
                    "aggregation": measure.public.effective_aggregation,
                    "value": value,
                    "record_count": len(grouped_rows),
                }
            )
    reverse = spec.sort_direction == "descending"
    if spec.chart_type == "time_line":
        output.sort(
            key=lambda row: (
                str(row["x"]),
                str(row["series"] or ""),
                str(row["measure"]),
            )
        )
    else:
        distinct_x = list(dict.fromkeys(str(row["x"]) for row in output))
        if spec.sort_by == "value":
            primary_selector = measures[0].public.selector
            primary_values = {
                str(row["x"]): float(row["value"])
                for row in output
                if row["measure_selector"] == primary_selector
            }
            distinct_x.sort(
                key=lambda value: primary_values.get(value, -math.inf),
                reverse=reverse,
            )
        else:
            distinct_x.sort(key=str.casefold, reverse=reverse)
        allowed_x = distinct_x[: spec.top_n]
        order = {value: index for index, value in enumerate(allowed_x)}
        measure_order = {
            measure.public.selector: index
            for index, measure in enumerate(measures)
        }
        output = [
            row for row in output if str(row["x"]) in order
        ]
        output.sort(
            key=lambda row: (
                order[str(row["x"])],
                measure_order[str(row["measure_selector"])],
            )
        )
    return tuple(output)


def _row_measure_value(row: DatasetRow, measure: _ResolvedMeasure) -> float | None:
    if measure.column is not None:
        return _number(row.values[measure.column])
    metric = measure.metric
    if metric is None:
        return None
    if metric.metric_type == "source" and metric.source is not None:
        return _number(row.values[metric.source.column])
    if metric.derived_metric is not None:
        return evaluate_derived_metric(metric.derived_metric, row.values).value
    return None


def _group_measure_value(
    rows: tuple[DatasetRow, ...],
    measure: _ResolvedMeasure,
) -> float | None:
    if measure.count_records:
        return float(len(rows))
    metric = measure.metric
    if (
        metric is not None
        and metric.derived_metric is not None
        and metric.derived_metric.calculation_level == "aggregate"
    ):
        return aggregate_derived_metric(
            metric.derived_metric,
            tuple(row.values for row in rows),
        ).value
    values = [
        value
        for row in rows
        if (value := _row_measure_value(row, measure)) is not None
    ]
    if not values:
        return None
    aggregation = measure.public.effective_aggregation
    try:
        return aggregate_row_values(values, aggregation)
    except ValueError as error:
        raise VisualizationError(str(error)) from error


def _render_chart(
    spec: VisualizationSpec,
    *,
    measures: tuple[_ResolvedMeasure, ...],
    supporting_data: tuple[dict[str, object], ...],
    chart_dir: Path,
) -> ManualChart:
    plt = _pyplot()
    figure, axis = plt.subplots(figsize=(9, 5.2), constrained_layout=True)
    try:
        if spec.chart_type == "time_line":
            _render_line(axis, supporting_data)
        elif spec.chart_type in {"category_bar", "category_bar_horizontal"}:
            _render_bars(
                axis,
                supporting_data,
                horizontal=spec.chart_type == "category_bar_horizontal",
            )
        elif spec.chart_type == "scatter":
            _render_scatter(axis, supporting_data)
            axis.set_xlabel(_safe_label(spec.x_column or "X"))
            axis.set_ylabel(_safe_label(measures[0].public.label))
        elif spec.chart_type == "histogram":
            axis.hist(
                [float(row["value"]) for row in supporting_data],
                bins=spec.bin_count,
                color="#4c78a8",
                edgecolor="white",
            )
            axis.set_xlabel(_safe_label(measures[0].public.label))
            axis.set_ylabel("Record count")
        elif spec.chart_type == "box":
            _render_box(axis, supporting_data, measures[0].public.label)
        else:
            raise VisualizationError("Chart type is unsupported.")
        axis.set_title(_safe_title(spec.title))
        if spec.scale == "log":
            axis.set_yscale("log")
        axis.grid(axis="y", alpha=0.2)
        filename = f"{secrets.token_hex(16)}.png"
        final_path = _chart_path(chart_dir, filename)
        temporary_path = chart_dir / f".{secrets.token_hex(16)}.part"
        try:
            figure.savefig(
                temporary_path,
                format="png",
                dpi=140,
                metadata={"Software": "AI Insight Reporter manual visualization builder"},
            )
            temporary_path.replace(final_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            raise
        return ManualChart(
            filename=filename,
            title=_safe_title(spec.title),
            alt_text=_safe_label(
                f"{_chart_type_label(spec.chart_type)} titled {spec.title}; "
                f"{len(supporting_data)} supporting rows."
            ),
            chart_type=spec.chart_type,
            record_count=len(supporting_data),
        )
    except VisualizationError:
        raise
    except Exception as error:
        raise VisualizationError("Manual chart generation failed safely.") from error
    finally:
        plt.close(figure)


def _render_line(axis: Any, rows: tuple[dict[str, object], ...]) -> None:
    x_values = sorted({str(row["x"]) for row in rows})
    x_positions = {value: index for index, value in enumerate(x_values)}
    keys = sorted(
        {
            (str(row["measure"]), str(row["series"] or ""))
            for row in rows
        }
    )
    for measure, series in keys:
        points = [
            (str(row["x"]), float(row["value"]))
            for row in rows
            if str(row["measure"]) == measure
            and str(row["series"] or "") == series
        ]
        label = measure if not series else f"{measure} — {series}"
        axis.plot(
            [x_positions[x_value] for x_value, _ in points],
            [value for _, value in points],
            marker="o",
            label=_safe_label(label),
        )
    axis.set_xticks(
        range(len(x_values)),
        [_safe_label(x_value) for x_value in x_values],
        rotation=30,
        ha="right",
    )
    if len(keys) > 1:
        axis.legend()


def _render_bars(
    axis: Any,
    rows: tuple[dict[str, object], ...],
    *,
    horizontal: bool,
) -> None:
    x_values = list(dict.fromkeys(str(row["x"]) for row in rows))
    measures = list(dict.fromkeys(str(row["measure"]) for row in rows))
    width = 0.8 / max(len(measures), 1)
    for measure_index, measure in enumerate(measures):
        values_by_x = {
            str(row["x"]): float(row["value"])
            for row in rows
            if str(row["measure"]) == measure
        }
        positions = [
            index - 0.4 + (width / 2) + (measure_index * width)
            for index in range(len(x_values))
        ]
        values = [values_by_x.get(x_value, math.nan) for x_value in x_values]
        if horizontal:
            axis.barh(positions, values, height=width, label=_safe_label(measure))
        else:
            axis.bar(positions, values, width=width, label=_safe_label(measure))
    labels = [_safe_label(value) for value in x_values]
    if horizontal:
        axis.set_yticks(range(len(x_values)), labels)
    else:
        axis.set_xticks(range(len(x_values)), labels, rotation=30, ha="right")
    if len(measures) > 1:
        axis.legend()


def _render_scatter(axis: Any, rows: tuple[dict[str, object], ...]) -> None:
    series_values = list(dict.fromkeys(str(row["series"] or "") for row in rows))
    for series in series_values:
        points = [
            (float(row["x"]), float(row["y"]))
            for row in rows
            if str(row["series"] or "") == series
        ]
        axis.scatter(
            [point[0] for point in points],
            [point[1] for point in points],
            alpha=0.75,
            label=_safe_label(series) if series else None,
        )
    if any(series_values):
        axis.legend()


def _render_box(
    axis: Any,
    rows: tuple[dict[str, object], ...],
    label: str,
) -> None:
    categories = list(
        dict.fromkeys(str(row["category"] or "") for row in rows)
    )
    if categories == [""]:
        axis.boxplot(
            [float(row["value"]) for row in rows],
            orientation="horizontal",
            patch_artist=True,
            boxprops={"facecolor": "#9ecae1"},
        )
        axis.set_yticks([1], [_safe_label(label)])
        return
    values = [
        [
            float(row["value"])
            for row in rows
            if str(row["category"] or "") == category
        ]
        for category in categories
    ]
    axis.boxplot(
        values,
        tick_labels=[_safe_label(category) for category in categories],
        patch_artist=True,
        boxprops={"facecolor": "#9ecae1"},
    )
    axis.tick_params(axis="x", labelrotation=30)


def _plotted_values(
    chart_type: str,
    rows: tuple[dict[str, object], ...],
) -> tuple[float, ...]:
    key = "y" if chart_type == "scatter" else "value"
    return tuple(
        number
        for row in rows
        if (number := _number(row.get(key))) is not None
    )


def _load_artifact(
    path: Path,
    *,
    dataset_id: str,
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
    allow_draft: bool,
) -> VisualizationArtifact:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VisualizationError("Saved visualization is unreadable.") from error
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") not in {1, 2, 3}
    ):
        raise VisualizationError("Saved visualization has an invalid shape.")
    if payload.get("dataset_id") != dataset_id or configuration.dataset_id != dataset_id:
        raise VisualizationError("Saved visualization belongs to another dataset.")
    visualization_id = payload.get("visualization_id")
    if visualization_id is None:
        if not allow_draft:
            raise VisualizationError("Saved visualization ID is missing.")
    elif (
        not isinstance(visualization_id, str)
        or _VISUALIZATION_ID.fullmatch(visualization_id) is None
        or path.stem != visualization_id
    ):
        raise VisualizationError("Saved visualization ID is invalid.")
    source = payload.get("source")
    if (
        not isinstance(source, dict)
        or source.get("sha256") != profile.source_sha256
        or source.get("format") != profile.source_format
        or source.get("filename") != profile.source_internal_filename
        or source.get("worksheet") != profile.source_table_name
    ):
        raise VisualizationError("Saved visualization source metadata is stale.")
    spec_payload = payload.get("spec")
    if not isinstance(spec_payload, dict):
        raise VisualizationError("Saved visualization specification is invalid.")
    spec = parse_visualization_spec(spec_payload)
    measures = tuple(
        _resolve_measure(selector, profile, configuration, spec)
        for selector in spec.measure_selectors
    )
    _validate_compatibility(spec, measures, profile, configuration)
    expected_measures = [measure.public.to_dict() for measure in measures]
    if payload.get("measures") != expected_measures:
        raise VisualizationError("Saved visualization measures are stale or tampered.")
    supporting_data = payload.get("supporting_data")
    if not isinstance(supporting_data, list) or not all(
        isinstance(row, dict) for row in supporting_data
    ):
        raise VisualizationError("Saved visualization supporting data is invalid.")
    chart = payload.get("chart")
    if (
        not isinstance(chart, dict)
        or not isinstance(chart.get("filename"), str)
        or _CHART_FILENAME.fullmatch(chart["filename"]) is None
        or chart.get("chart_type") != spec.chart_type
        or not isinstance(chart.get("title"), str)
        or not isinstance(chart.get("alt_text"), str)
        or not isinstance(chart.get("record_count"), int)
        or chart["record_count"] < 1
    ):
        raise VisualizationError("Saved visualization chart metadata is invalid.")
    classification = payload.get("classification")
    expected_classification = (
        "kpi"
        if all(measure.public.role == "configured_kpi" for measure in measures)
        else "supplementary"
    )
    if classification != expected_classification:
        raise VisualizationError("Saved visualization classification is invalid.")
    source_columns = payload.get("source_columns")
    expected_source_columns = _source_columns(spec, measures, configuration)
    if not isinstance(source_columns, list) or not all(
        isinstance(column, str) and profile.column(column) is not None
        for column in source_columns
    ) or tuple(source_columns) != expected_source_columns:
        raise VisualizationError("Saved visualization source columns are invalid.")
    filtered_count = payload.get("filtered_record_count")
    created_at = payload.get("created_at")
    if (
        not isinstance(filtered_count, int)
        or filtered_count < 0
        or not isinstance(created_at, str)
    ):
        raise VisualizationError("Saved visualization metadata is invalid.")
    assistant = (
        _validate_assistant_metadata(payload.get("assistant"))
        if payload.get("schema_version") == 2
        else None
    )
    return VisualizationArtifact(
        schema_version=int(payload["schema_version"]),
        visualization_id=visualization_id,
        dataset_id=dataset_id,
        classification=classification,
        source=dict(source),
        spec=spec,
        measures=tuple(measure.public for measure in measures),
        supporting_data=tuple(dict(row) for row in supporting_data),
        source_columns=tuple(source_columns),
        filtered_record_count=filtered_count,
        chart=ManualChart(
            filename=chart["filename"],
            title=str(chart.get("title", "")),
            alt_text=str(chart.get("alt_text", "")),
            chart_type=str(chart.get("chart_type", "")),
            record_count=chart["record_count"],
        ),
        created_at=created_at,
        assistant=assistant,
    )


def _validate_assistant_metadata(
    value: object,
) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "method",
        "model",
        "user_request",
        "confidence",
        "rationale",
    }:
        raise VisualizationError("Visualization assistant metadata is invalid.")
    model = value.get("model")
    user_request = value.get("user_request")
    confidence = value.get("confidence")
    rationale = value.get("rationale")
    if (
        value.get("method") != "ollama_assisted"
        or not isinstance(model, str)
        or not model.strip()
        or len(model) > 200
        or not isinstance(user_request, str)
        or not user_request.strip()
        or len(user_request) > 2_000
        or isinstance(confidence, bool)
        or not isinstance(confidence, int | float)
        or not math.isfinite(float(confidence))
        or not 0 <= float(confidence) <= 1
        or not isinstance(rationale, list)
        or not 1 <= len(rationale) <= 5
        or not all(
            isinstance(item, str) and item.strip() and len(item) <= 300
            for item in rationale
        )
    ):
        raise VisualizationError("Visualization assistant metadata is invalid.")
    return {
        "method": "ollama_assisted",
        "model": model.strip(),
        "user_request": user_request.strip(),
        "confidence": float(confidence),
        "rationale": [item.strip() for item in rationale],
    }


def _source_columns(
    spec: VisualizationSpec,
    measures: tuple[_ResolvedMeasure, ...],
    configuration: BusinessConfiguration,
) -> tuple[str, ...]:
    columns: list[str] = []
    for value in (
        spec.x_column,
        spec.series_column,
        spec.filter_column,
        configuration.date_column if spec.date_start or spec.date_end else None,
    ):
        if value is not None and value not in columns:
            columns.append(value)
    for measure in measures:
        for column in measure.public.source_columns:
            if column not in columns:
                columns.append(column)
    return tuple(columns)


def _source_metadata(view: DatasetView) -> dict[str, object]:
    source = view.sources[0]
    return {
        "source_id": source.source_id,
        "filename": source.internal_filename,
        "format": source.format,
        "sha256": source.sha256,
        "worksheet": source.table_name,
    }


def _validate_dataset(
    view: DatasetView,
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
) -> None:
    if len(view.sources) != 1:
        raise VisualizationError(
            "Manual visualizations require one selected source table."
        )
    source = view.sources[0]
    if (
        source.sha256 != profile.source_sha256
        or source.sha256 != configuration.source_sha256
    ):
        raise VisualizationError(
            "Visualization inputs do not refer to the same immutable dataset."
        )


def _dataset_visualization_dir(
    root: Path,
    dataset_id: str,
    *,
    create: bool,
) -> Path:
    if _DATASET_ID.fullmatch(dataset_id) is None:
        raise VisualizationError("Visualization dataset ID is invalid.")
    root = root.resolve()
    directory = (root / dataset_id).resolve()
    if directory.parent != root:
        raise VisualizationError("Visualization directory escaped its configured root.")
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise VisualizationError("Visualization directory is unavailable.")
    return directory


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.part"
    encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    try:
        temporary.write_text(f"{encoded}\n", encoding="utf-8")
        temporary.replace(path)
    except Exception as error:
        temporary.unlink(missing_ok=True)
        raise VisualizationError("Visualization artifact could not be saved.") from error


def _delete_expired_previews(preview_dir: Path, *, chart_dir: Path) -> None:
    cutoff = time.time() - _PREVIEW_RETENTION_SECONDS
    for path in preview_dir.glob("[0-9a-f]" * 32 + ".json"):
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            chart = payload.get("chart") if isinstance(payload, dict) else None
            filename = chart.get("filename") if isinstance(chart, dict) else None
            if isinstance(filename, str):
                delete_chart(filename, chart_dir=chart_dir)
            path.unlink(missing_ok=True)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue


def _secure_directory(directory: Path) -> Path:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        resolved = directory.resolve(strict=True)
    except OSError as error:
        raise VisualizationError("Visualization output directory is unavailable.") from error
    if not resolved.is_dir():
        raise VisualizationError("Visualization output path is not a directory.")
    return resolved


def _chart_path(chart_dir: Path, filename: str) -> Path:
    if _CHART_FILENAME.fullmatch(filename) is None:
        raise VisualizationError("Generated chart filename is invalid.")
    path = (chart_dir / filename).resolve()
    if path.parent != chart_dir.resolve():
        raise VisualizationError("Generated chart path escaped its configured directory.")
    return path


def _parse_datetime(value: str) -> datetime | None:
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(candidate, "%Y/%m/%d")
        except ValueError:
            return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed


def _period_label(value: datetime, granularity: str) -> str:
    if granularity == "day":
        return value.strftime("%Y-%m-%d")
    if granularity == "month":
        return value.strftime("%Y-%m")
    if granularity == "quarter":
        return f"{value.year}-Q{((value.month - 1) // 3) + 1}"
    return str(value.year)


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and _is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_missing(value: str) -> bool:
    return value.strip().casefold() in _MISSING_MARKERS


def _required_text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VisualizationError(f"{label} is required.")
    text = value.strip()
    if len(text) > maximum:
        raise VisualizationError(f"{label} must be at most {maximum} characters.")
    return text


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _bounded_optional_text(
    value: object,
    label: str,
    maximum: int,
) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise VisualizationError(f"{label} is invalid.")
    text = value.strip()
    if len(text) > maximum:
        raise VisualizationError(
            f"{label} must be at most {maximum} characters."
        )
    return text


def _optional_date(value: object, label: str) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as error:
        raise VisualizationError(f"Visualization {label} is invalid.") from error


def _choice(value: object, choices: tuple[str, ...], label: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise VisualizationError(f"Visualization {label} is invalid.")
    return value


def _bounded_integer(
    value: object,
    label: str,
    minimum: int,
    maximum: int,
    default: int,
) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(str(value))
    except ValueError as error:
        raise VisualizationError(f"{label} must be a whole number.") from error
    if not minimum <= number <= maximum:
        raise VisualizationError(
            f"{label} must be between {minimum} and {maximum}."
        )
    return number


def _boolean(value: object) -> bool:
    return value is True or (
        isinstance(value, str) and value in {"yes", "true", "1", "on"}
    )


def _safe_title(value: object) -> str:
    return _safe_label(value, maximum=_MAX_TITLE_CHARACTERS)


def _safe_label(value: object, *, maximum: int = _MAX_LABEL_CHARACTERS) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    cleaned = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in normalized
    )
    cleaned = " ".join(cleaned.split()).replace("$", r"\$")
    if len(cleaned) > maximum:
        cleaned = f"{cleaned[: maximum - 1]}…"
    return cleaned


def _chart_type_label(chart_type: str) -> str:
    return {
        "time_line": "time-series line chart",
        "category_bar": "category bar chart",
        "category_bar_horizontal": "horizontal category bar chart",
        "scatter": "scatter plot",
        "histogram": "distribution histogram",
        "box": "box-and-outlier chart",
    }[chart_type]


def _pyplot() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as pyplot

    return pyplot
