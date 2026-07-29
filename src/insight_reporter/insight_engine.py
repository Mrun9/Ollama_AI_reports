"""Reproducible, Python-only factual insight calculations."""

import json
import math
import secrets
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from insight_reporter.business_config import BusinessConfiguration
from insight_reporter.dataset_profile import ColumnType, DatasetProfile
from insight_reporter.dataset_view import (
    CsvDatasetView,
    DatasetView,
    DatasetViewError,
    SourceManifest,
)
from insight_reporter.derived_metrics import (
    aggregate_derived_metric,
    evaluate_derived_metric,
)

_MISSING_MARKERS = frozenset({"", "na", "n/a", "null", "none", "nan"})
_MIN_GENERAL_RECORDS = 5
_MIN_PERIOD_RECORDS = 2
_MIN_TREND_PERIODS = 3
_MIN_SEGMENT_RECORDS = 2
_MIN_ANOMALY_RECORDS = 4
_MIN_CORRELATION_PAIRS = 3
_MIN_BENCHMARK_RECORDS = 3


class InsightEngineError(ValueError):
    """Raised when deterministic analysis cannot safely use retained inputs."""


@dataclass(frozen=True)
class Insight:
    """One traceable factual observation generated without a language model."""

    id: str
    metric_id: str
    type: str
    metric: str
    observation: dict[str, object]
    source_columns: tuple[str, ...]
    filters: dict[str, object]
    record_count: int
    confidence: str
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "metric_id": self.metric_id,
            "type": self.type,
            "metric": self.metric,
            "observation": self.observation,
            "source_columns": list(self.source_columns),
            "filters": self.filters,
            "record_count": self.record_count,
            "confidence": self.confidence,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class InsightReport:
    """Deterministic insight artifact tied to one dataset and configuration."""

    schema_version: int
    dataset_id: str
    source_sha256: str
    sources: tuple[dict[str, object], ...]
    configuration_schema_version: int
    primary_metric_id: str
    metric_definitions: tuple[dict[str, object], ...]
    insights: tuple[Insight, ...]

    @property
    def metric_definition(self) -> dict[str, object]:
        """Return the primary definition for v2 callers."""

        for definition in self.metric_definitions:
            if definition.get("metric_id") == self.primary_metric_id:
                return definition
        return self.metric_definitions[0]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "source_sha256": self.source_sha256,
            "sources": list(self.sources),
            "configuration_schema_version": self.configuration_schema_version,
            "primary_metric_id": self.primary_metric_id,
            "metric_definitions": list(self.metric_definitions),
            # Retained for readers that still expect one KPI definition.
            "metric_definition": self.metric_definition,
            "insights": [insight.to_dict() for insight in self.insights],
        }


@dataclass(frozen=True)
class _Row:
    number: int
    values: dict[str, str]


@dataclass(frozen=True)
class _TemporalContext:
    granularity: str
    groups: dict[str, tuple[tuple[_Row, float], ...]]


class _Collector:
    def __init__(self) -> None:
        self.insights: list[Insight] = []
        self.metric_id = "DATASET"

    def add(
        self,
        *,
        insight_type: str,
        metric: str,
        observation: dict[str, object],
        source_columns: tuple[str, ...],
        record_count: int,
        confidence: str = "high",
        filters: dict[str, object] | None = None,
        limitations: tuple[str, ...] = (),
    ) -> None:
        self.insights.append(
            Insight(
                id=f"INS-{len(self.insights) + 1:03d}",
                metric_id=self.metric_id,
                type=insight_type,
                metric=metric,
                observation=observation,
                source_columns=source_columns,
                filters=filters or {},
                record_count=record_count,
                confidence=confidence,
                limitations=limitations,
            )
        )


def generate_insights(
    source: Path | DatasetView,
    *,
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
) -> InsightReport:
    """Generate deterministic evidence for every configured KPI."""

    if isinstance(source, Path):
        try:
            view = CsvDatasetView.from_path(source)
        except DatasetViewError as error:
            raise InsightEngineError(str(error)) from error
    else:
        view = source
    if len(view.sources) != 1:
        raise InsightEngineError(
            "Multi-source joins require an explicit relationship plan."
        )
    dataset_rows = view.iter_rows()
    rows = tuple(_Row(row.number, dict(row.values)) for row in dataset_rows)
    headers = view.headers
    source_sha256 = view.sources[0].sha256
    _validate_inputs(
        headers=headers,
        source=view.sources[0],
        profile=profile,
        configuration=configuration,
    )

    collector = _Collector()
    _add_missing_data_insights(collector, profile)
    for metric in configuration.metrics:
        metric_configuration = configuration.for_metric(metric.metric_id)
        collector.metric_id = metric.metric_id
        if len(rows) < _MIN_GENERAL_RECORDS:
            collector.add(
                insight_type="insufficient_data_warning",
                metric=metric_configuration.primary_kpi,
                observation={
                    "reason": "small_dataset",
                    "available_records": len(rows),
                    "recommended_minimum": _MIN_GENERAL_RECORDS,
                },
                source_columns=_metric_source_columns(metric_configuration),
                record_count=len(rows),
                confidence="high",
                limitations=(
                    "Dataset-level conclusions may be unstable with fewer than 5 rows.",
                ),
            )

        temporal = _prepare_temporal_context(
            collector,
            rows=rows,
            configuration=metric_configuration,
        )
        if temporal is not None:
            _add_period_change(collector, temporal, metric_configuration)
            _add_trend(collector, temporal, metric_configuration)

        for category_column in metric_configuration.category_columns:
            _add_segment_ranking(
                collector,
                rows=rows,
                category_column=category_column,
                configuration=metric_configuration,
            )
            if temporal is not None:
                _add_segment_contribution(
                    collector,
                    temporal=temporal,
                    category_column=category_column,
                    configuration=metric_configuration,
                )

        _add_anomalies(
            collector, rows=rows, configuration=metric_configuration
        )
        _add_correlations(
            collector,
            rows=rows,
            profile=profile,
            configuration=metric_configuration,
        )
        _add_benchmark_breaches(
            collector, rows=rows, configuration=metric_configuration
        )

    return InsightReport(
        schema_version=4,
        dataset_id=configuration.dataset_id,
        source_sha256=source_sha256,
        sources=tuple(source.to_dict() for source in configuration.sources),
        configuration_schema_version=configuration.schema_version,
        primary_metric_id=configuration.primary_metric_id,
        metric_definitions=tuple(
            _metric_definition(configuration.for_metric(metric.metric_id))
            for metric in configuration.metrics
        ),
        insights=tuple(collector.insights),
    )


def save_insight_report(report: InsightReport, *, insight_dir: Path) -> Path:
    """Atomically save the reproducible insight artifact outside the static tree."""

    insight_dir.mkdir(parents=True, exist_ok=True)
    final_path = insight_dir / f"{report.dataset_id}.json"
    temporary_path = insight_dir / f".{report.dataset_id}.{secrets.token_hex(8)}.part"
    payload = json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
    try:
        temporary_path.write_text(f"{payload}\n", encoding="utf-8")
        temporary_path.replace(final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return final_path


def _validate_inputs(
    *,
    headers: tuple[str, ...],
    source: SourceManifest,
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
) -> None:
    configured_source = configuration.sources[0]
    if (
        source.sha256 != profile.source_sha256
        or source.sha256 != configuration.source_sha256
    ):
        raise InsightEngineError("Dataset hash does not match the profile and configuration.")
    if (
        source.source_id != configured_source.source_id
        or source.format != configured_source.format
        or source.table_name != configured_source.table_name
        or source.row_count != configured_source.row_count
        or source.column_count != configured_source.column_count
    ):
        raise InsightEngineError(
            "Selected source table does not match the saved configuration."
        )
    selected_columns = set(configuration.category_columns)
    for metric in configuration.metrics:
        metric_configuration = configuration.for_metric(metric.metric_id)
        if metric_configuration.metric_type == "source":
            if metric_configuration.primary_kpi not in profile.kpi_candidates:
                raise InsightEngineError(
                    "Configured KPI is not a measurable profile candidate."
                )
            if metric_configuration.derived_metric is not None:
                raise InsightEngineError(
                    "Source KPI configuration contains a derived formula."
                )
        elif metric_configuration.metric_type == "derived":
            if metric_configuration.derived_metric is None:
                raise InsightEngineError("Derived KPI configuration has no formula.")
            for column_name in metric_configuration.derived_metric.source_columns:
                column = profile.column(column_name)
                if column is None or column.inferred_type is not ColumnType.NUMERIC:
                    raise InsightEngineError(
                        "Derived KPI source columns are not numeric."
                    )
        else:
            raise InsightEngineError("Configured KPI type is unsupported.")
        selected_columns.update(_metric_source_columns(metric_configuration))
    if configuration.date_column is not None:
        selected_columns.add(configuration.date_column)
    if not selected_columns.issubset(headers):
        raise InsightEngineError("Configured analysis columns are missing from the CSV.")


def _add_missing_data_insights(collector: _Collector, profile: DatasetProfile) -> None:
    for column in profile.columns:
        if column.missing_count == 0:
            continue
        collector.add(
            insight_type="missing_data_warning",
            metric=column.name,
            observation={
                "missing_count": column.missing_count,
                "missing_percentage": _clean(column.missing_percentage),
                "total_records": profile.row_count,
            },
            source_columns=(column.name,),
            record_count=profile.row_count,
            limitations=("Configured missing-value markers are treated as missing.",),
        )


def _prepare_temporal_context(
    collector: _Collector,
    *,
    rows: tuple[_Row, ...],
    configuration: BusinessConfiguration,
) -> _TemporalContext | None:
    date_column = configuration.date_column
    metric = configuration.primary_kpi
    temporal_types = ["period_change", "trend", "segment_contribution"]
    if date_column is None:
        collector.add(
            insight_type="analysis_skipped",
            metric=metric,
            observation={"reason": "no_date_column", "analyses": temporal_types},
            source_columns=_metric_source_columns(configuration),
            record_count=0,
            limitations=("Temporal analysis requires a user-confirmed date column.",),
        )
        return None

    if any(_is_missing(row.values[date_column]) for row in rows):
        missing_count = sum(_is_missing(row.values[date_column]) for row in rows)
        collector.add(
            insight_type="analysis_skipped",
            metric=metric,
            observation={
                "reason": "missing_dates",
                "missing_date_count": missing_count,
                "analyses": temporal_types,
            },
            source_columns=_source_columns(configuration, date_column),
            record_count=len(rows),
            limitations=("Temporal analysis is skipped rather than inferring missing dates.",),
        )
        return None

    parsed: list[tuple[_Row, datetime, float]] = []
    for row in rows:
        parsed_date = _parse_datetime(row.values[date_column])
        if parsed_date is None:
            collector.add(
                insight_type="analysis_skipped",
                metric=metric,
                observation={"reason": "unparseable_date", "analyses": temporal_types},
                source_columns=_source_columns(configuration, date_column),
                record_count=len(rows),
                limitations=("Temporal analysis requires consistently parseable dates.",),
            )
            return None
        number = _metric_group_value(row, configuration)
        if number is not None:
            parsed.append((row, parsed_date, number))

    if not parsed:
        collector.add(
            insight_type="analysis_skipped",
            metric=metric,
            observation={"reason": "no_dated_kpi_values", "analyses": temporal_types},
            source_columns=_source_columns(configuration, date_column),
            record_count=0,
            limitations=("Temporal analysis requires non-missing KPI values.",),
        )
        return None

    distinct_months = {_period_label(value, "month") for _, value, _ in parsed}
    granularity = "month" if len(distinct_months) >= 2 else "day"
    mutable_groups: dict[str, list[tuple[_Row, float]]] = {}
    for row, parsed_date, number in parsed:
        period = _period_label(parsed_date, granularity)
        mutable_groups.setdefault(period, []).append((row, number))
    return _TemporalContext(
        granularity=granularity,
        groups={key: tuple(value) for key, value in mutable_groups.items()},
    )


def _add_period_change(
    collector: _Collector,
    temporal: _TemporalContext,
    configuration: BusinessConfiguration,
) -> None:
    periods = sorted(temporal.groups)
    metric = configuration.primary_kpi
    date_column = configuration.date_column
    assert date_column is not None
    if len(periods) < 2:
        _add_insufficient_warning(
            collector,
            metric=metric,
            analysis="period_change",
            available=len(periods),
            required=2,
            source_columns=_source_columns(configuration, date_column),
        )
        return

    previous_period, current_period = periods[-2:]
    previous_values = temporal.groups[previous_period]
    current_values = temporal.groups[current_period]
    if min(len(previous_values), len(current_values)) < _MIN_PERIOD_RECORDS:
        _add_insufficient_warning(
            collector,
            metric=metric,
            analysis="period_change",
            available=min(len(previous_values), len(current_values)),
            required=_MIN_PERIOD_RECORDS,
            source_columns=_source_columns(configuration, date_column),
        )
        return

    previous_total = _aggregate_metric(previous_values, configuration)
    current_total = _aggregate_metric(current_values, configuration)
    if previous_total is None or current_total is None:
        collector.add(
            insight_type="analysis_skipped",
            metric=metric,
            observation={
                "reason": "undefined_derived_aggregate",
                "analysis": "period_change",
            },
            source_columns=_source_columns(configuration, date_column),
            record_count=len(previous_values) + len(current_values),
            limitations=(
                "The configured derived aggregation produced no finite comparison value.",
            ),
        )
        return
    absolute_change = current_total - previous_total
    limitations = [_aggregation_limitation(configuration, "calendar period")]
    if previous_total == 0:
        percentage_change = None
        limitations.append("Percentage change is not calculated because the prior total is zero.")
    else:
        percentage_change = _clean((absolute_change / previous_total) * 100)

    collector.add(
        insight_type="period_change",
        metric=metric,
        observation={
            "aggregation": _metric_aggregation(configuration),
            "period_granularity": temporal.granularity,
            "previous_period": previous_period,
            "previous_value": _clean(previous_total),
            "current_period": current_period,
            "current_value": _clean(current_total),
            "absolute_change": _clean(absolute_change),
            "percentage_change": percentage_change,
            "direction": _direction(absolute_change),
            "favorable": _favorable(_direction(absolute_change), configuration.kpi_direction),
        },
        source_columns=_source_columns(configuration, date_column),
        filters={"periods": [previous_period, current_period]},
        record_count=len(previous_values) + len(current_values),
        confidence=_count_confidence(min(len(previous_values), len(current_values))),
        limitations=tuple(limitations),
    )


def _add_trend(
    collector: _Collector,
    temporal: _TemporalContext,
    configuration: BusinessConfiguration,
) -> None:
    metric = configuration.primary_kpi
    date_column = configuration.date_column
    assert date_column is not None
    eligible = [
        (period, temporal.groups[period])
        for period in sorted(temporal.groups)
        if len(temporal.groups[period]) >= _MIN_PERIOD_RECORDS
    ]
    if len(eligible) < _MIN_TREND_PERIODS:
        _add_insufficient_warning(
            collector,
            metric=metric,
            analysis="trend",
            available=len(eligible),
            required=_MIN_TREND_PERIODS,
            source_columns=_source_columns(configuration, date_column),
        )
        return

    period_values = [_aggregate_metric(values, configuration) for _, values in eligible]
    if any(value is None for value in period_values):
        collector.add(
            insight_type="analysis_skipped",
            metric=metric,
            observation={"reason": "undefined_derived_aggregate", "analysis": "trend"},
            source_columns=_source_columns(configuration, date_column),
            record_count=sum(len(values) for _, values in eligible),
            limitations=(
                "The configured derived aggregation produced a non-finite period value.",
            ),
        )
        return
    safe_period_values = [value for value in period_values if value is not None]
    slope, r_squared = _linear_trend(safe_period_values)
    direction = _direction(slope)
    collector.add(
        insight_type="trend",
        metric=metric,
        observation={
            "aggregation": _metric_aggregation(configuration),
            "period_granularity": temporal.granularity,
            "direction": direction,
            "slope_per_period": _clean(slope),
            "r_squared": _clean(r_squared),
            "favorable": _favorable(direction, configuration.kpi_direction),
            "period_values": [
                {"period": period, "value": _clean(value)}
                for (period, _), value in zip(eligible, safe_period_values, strict=True)
            ],
        },
        source_columns=_source_columns(configuration, date_column),
        record_count=sum(len(values) for _, values in eligible),
        confidence=_count_confidence(sum(len(values) for _, values in eligible)),
        limitations=(
            _aggregation_limitation(configuration, "period"),
            "Trend describes a linear pattern over observed periods and does not imply causation.",
        ),
    )


def _add_segment_ranking(
    collector: _Collector,
    *,
    rows: tuple[_Row, ...],
    category_column: str,
    configuration: BusinessConfiguration,
) -> None:
    metric = configuration.primary_kpi
    groups: dict[str, list[tuple[_Row, float]]] = {}
    for row in rows:
        segment = row.values[category_column].strip()
        number = _metric_group_value(row, configuration)
        if not segment or _is_missing(segment) or number is None:
            continue
        groups.setdefault(segment, []).append((row, number))

    eligible = {
        segment: values
        for segment, values in groups.items()
        if len(values) >= _MIN_SEGMENT_RECORDS
    }
    if len(eligible) < 2:
        _add_insufficient_warning(
            collector,
            metric=metric,
            analysis=f"segment_ranking:{category_column}",
            available=len(eligible),
            required=2,
            source_columns=_source_columns(configuration, category_column),
        )
        return

    segment_values = [
        (segment, entries, _aggregate_metric(tuple(entries), configuration))
        for segment, entries in eligible.items()
    ]
    segment_values = [item for item in segment_values if item[2] is not None]
    if len(segment_values) < 2:
        _add_insufficient_warning(
            collector,
            metric=metric,
            analysis=f"segment_ranking:{category_column}",
            available=len(segment_values),
            required=2,
            source_columns=_source_columns(configuration, category_column),
        )
        return

    ranked = sorted(
        (
            {
                "segment": segment,
                "value": _clean(value),
                "record_count": len(entries),
            }
            for segment, entries, value in segment_values
            if value is not None
        ),
        key=lambda item: (-float(item["value"]), str(item["segment"]).casefold()),
    )
    collector.add(
        insight_type="segment_ranking",
        metric=metric,
        observation={
            "aggregation": _metric_aggregation(configuration),
            "category_column": category_column,
            "top_segment": ranked[0],
            "bottom_segment": ranked[-1],
            "ranking": ranked,
        },
        source_columns=_source_columns(configuration, category_column),
        record_count=sum(len(entries) for _, entries, _ in segment_values),
        confidence=_count_confidence(sum(len(entries) for _, entries, _ in segment_values)),
        limitations=(
            _aggregation_limitation(configuration, "segment"),
            "Segments with fewer than two valid KPI records are excluded.",
        ),
    )


def _add_segment_contribution(
    collector: _Collector,
    *,
    temporal: _TemporalContext,
    category_column: str,
    configuration: BusinessConfiguration,
) -> None:
    periods = sorted(temporal.groups)
    metric = configuration.primary_kpi
    date_column = configuration.date_column
    assert date_column is not None
    if _metric_aggregation(configuration) != "sum":
        collector.add(
            insight_type="analysis_skipped",
            metric=metric,
            observation={
                "reason": "non_additive_metric",
                "analysis": "segment_contribution",
                "category_column": category_column,
            },
            source_columns=_source_columns(configuration, date_column, category_column),
            record_count=0,
            limitations=(
                "Contribution-to-change requires an additive sum metric; means and ratios "
                "cannot be reconciled as additive contributions.",
            ),
        )
        return
    if len(periods) < 2:
        return
    previous_period, current_period = periods[-2:]
    previous_rows = temporal.groups[previous_period]
    current_rows = temporal.groups[current_period]
    if min(len(previous_rows), len(current_rows)) < _MIN_PERIOD_RECORDS:
        return

    def segment_totals(
        values: tuple[tuple[_Row, float], ...],
    ) -> dict[str, tuple[float, int]]:
        buckets: dict[str, list[tuple[_Row, float]]] = {}
        for row, number in values:
            segment = row.values[category_column].strip()
            if segment and not _is_missing(segment):
                buckets.setdefault(segment, []).append((row, number))
        totals: dict[str, tuple[float, int]] = {}
        for segment, entries in buckets.items():
            value = _aggregate_metric(tuple(entries), configuration)
            if value is not None:
                totals[segment] = (value, len(entries))
        return totals

    previous = segment_totals(previous_rows)
    current = segment_totals(current_rows)
    segments = sorted(set(previous) | set(current), key=str.casefold)
    if len(segments) < 2:
        return

    changes: list[dict[str, object]] = []
    overall_change = 0.0
    for segment in segments:
        previous_value, previous_count = previous.get(segment, (0.0, 0))
        current_value, current_count = current.get(segment, (0.0, 0))
        change = current_value - previous_value
        overall_change += change
        changes.append(
            {
                "segment": segment,
                "previous_value": _clean(previous_value),
                "current_value": _clean(current_value),
                "absolute_change": _clean(change),
                "previous_record_count": previous_count,
                "current_record_count": current_count,
            }
        )

    limitations = [
        "KPI values use sum aggregation by segment and missing category values are excluded."
    ]
    if math.isclose(overall_change, 0.0, abs_tol=1e-12):
        for item in changes:
            item["contribution_percentage"] = None
        reconciled_percentage = None
        percentage_status = "not_calculated_zero_overall_change"
        limitations.append(
            "Contribution percentages are not calculated because overall change is zero."
        )
    else:
        for item in changes:
            item["contribution_percentage"] = _clean(
                (float(item["absolute_change"]) / overall_change) * 100
            )
        rounded_total = math.fsum(
            float(item["contribution_percentage"]) for item in changes
        )
        # Assign any floating-point rounding residue deterministically so the
        # displayed contributions reconcile to exactly 100 percent.
        changes[-1]["contribution_percentage"] = _clean(
            float(changes[-1]["contribution_percentage"]) + (100.0 - rounded_total)
        )
        reconciled_percentage = 100.0
        percentage_status = "calculated"

    changes.sort(
        key=lambda item: (-float(item["absolute_change"]), str(item["segment"]).casefold())
    )
    collector.add(
        insight_type="segment_contribution",
        metric=metric,
        observation={
            "aggregation": _metric_aggregation(configuration),
            "category_column": category_column,
            "previous_period": previous_period,
            "current_period": current_period,
            "overall_change": _clean(overall_change),
            "percentage_status": percentage_status,
            "reconciled_percentage_total": reconciled_percentage,
            "contributions": changes,
        },
        source_columns=_source_columns(configuration, date_column, category_column),
        filters={"periods": [previous_period, current_period]},
        record_count=len(previous_rows) + len(current_rows),
        confidence=_count_confidence(min(len(previous_rows), len(current_rows))),
        limitations=tuple(limitations),
    )


def _add_anomalies(
    collector: _Collector,
    *,
    rows: tuple[_Row, ...],
    configuration: BusinessConfiguration,
) -> None:
    metric = configuration.primary_kpi
    values = [
        (row.number, number)
        for row in rows
        if (number := _metric_value(row, configuration)) is not None
    ]
    if len(values) < _MIN_ANOMALY_RECORDS:
        _add_insufficient_warning(
            collector,
            metric=metric,
            analysis="iqr_anomaly_detection",
            available=len(values),
            required=_MIN_ANOMALY_RECORDS,
            source_columns=_metric_source_columns(configuration),
        )
        return

    numeric_values = [value for _, value in values]
    first_quartile, _, third_quartile = statistics.quantiles(
        numeric_values, n=4, method="inclusive"
    )
    iqr = third_quartile - first_quartile
    lower_bound = first_quartile - 1.5 * iqr
    upper_bound = third_quartile + 1.5 * iqr
    anomalies = [
        {"row_number": row_number, "value": _clean(value)}
        for row_number, value in values
        if value < lower_bound or value > upper_bound
    ]
    collector.add(
        insight_type="iqr_anomaly_detection",
        metric=metric,
        observation={
            "method": "Tukey 1.5 IQR",
            "q1": _clean(first_quartile),
            "q3": _clean(third_quartile),
            "iqr": _clean(iqr),
            "lower_bound": _clean(lower_bound),
            "upper_bound": _clean(upper_bound),
            "anomaly_count": len(anomalies),
            "anomalies": anomalies,
        },
        source_columns=_metric_source_columns(configuration),
        record_count=len(values),
        confidence=_count_confidence(len(values)),
        limitations=(
            "IQR flags statistical outliers, not errors or causal events.",
            "Missing KPI values are excluded.",
        ),
    )


def _add_correlations(
    collector: _Collector,
    *,
    rows: tuple[_Row, ...],
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
) -> None:
    metric = configuration.primary_kpi
    for column in profile.columns:
        if (
            column.name == metric
            or column.inferred_type is not ColumnType.NUMERIC
            or column.is_constant
        ):
            continue
        pairs = [
            (left, right)
            for row in rows
            if (left := _metric_value(row, configuration)) is not None
            and (right := _number(row.values[column.name])) is not None
        ]
        if len(pairs) < _MIN_CORRELATION_PAIRS:
            _add_insufficient_warning(
                collector,
                metric=metric,
                analysis=f"correlation:{column.name}",
                available=len(pairs),
                required=_MIN_CORRELATION_PAIRS,
                source_columns=_metric_first_source_columns(configuration, column.name),
            )
            continue
        coefficient = _pearson(pairs)
        if coefficient is None:
            continue
        collector.add(
            insight_type="numeric_correlation",
            metric=metric,
            observation={
                "associated_metric": column.name,
                "coefficient": _clean(coefficient),
                "direction": _direction(coefficient),
                "strength": _correlation_strength(coefficient),
                "relationship_label": "association",
            },
            source_columns=_metric_first_source_columns(configuration, column.name),
            record_count=len(pairs),
            confidence=_count_confidence(len(pairs)),
            limitations=(
                "Pearson correlation measures linear association and does not imply causation.",
                "Rows missing either numeric value are excluded pairwise.",
            ),
        )


def _add_benchmark_breaches(
    collector: _Collector,
    *,
    rows: tuple[_Row, ...],
    configuration: BusinessConfiguration,
) -> None:
    target = configuration.target_or_benchmark
    if target is None:
        return
    metric = configuration.primary_kpi
    values = [
        number
        for row in rows
        if (number := _metric_value(row, configuration)) is not None
    ]
    if len(values) < _MIN_BENCHMARK_RECORDS:
        _add_insufficient_warning(
            collector,
            metric=metric,
            analysis="benchmark_breach",
            available=len(values),
            required=_MIN_BENCHMARK_RECORDS,
            source_columns=_metric_source_columns(configuration),
        )
        return

    if configuration.kpi_direction == "higher":
        breaches = [value for value in values if value < target]
        condition = "value < target"
    else:
        breaches = [value for value in values if value > target]
        condition = "value > target"
    collector.add(
        insight_type="benchmark_breach",
        metric=metric,
        observation={
            "target": _clean(target),
            "kpi_direction": configuration.kpi_direction,
            "breach_condition": condition,
            "breach_count": len(breaches),
            "non_breach_count": len(values) - len(breaches),
            "breach_percentage": _clean((len(breaches) / len(values)) * 100),
        },
        source_columns=_metric_source_columns(configuration),
        record_count=len(values),
        confidence=_count_confidence(len(values)),
        limitations=("Benchmark status is evaluated per non-missing row, not on an aggregate.",),
    )


def _add_insufficient_warning(
    collector: _Collector,
    *,
    metric: str,
    analysis: str,
    available: int,
    required: int,
    source_columns: tuple[str, ...],
) -> None:
    collector.add(
        insight_type="insufficient_data_warning",
        metric=metric,
        observation={
            "analysis": analysis,
            "available_records_or_periods": available,
            "required_records_or_periods": required,
        },
        source_columns=source_columns,
        record_count=available,
        limitations=("The calculation was skipped because evidence was insufficient.",),
    )


def _metric_source_columns(configuration: BusinessConfiguration) -> tuple[str, ...]:
    if configuration.metric_type == "derived" and configuration.derived_metric is not None:
        return configuration.derived_metric.source_columns
    return (configuration.primary_kpi,)


def _source_columns(
    configuration: BusinessConfiguration, *additional: str
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*additional, *_metric_source_columns(configuration))))


def _metric_first_source_columns(
    configuration: BusinessConfiguration, *additional: str
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*_metric_source_columns(configuration), *additional)))


def _metric_value(row: _Row, configuration: BusinessConfiguration) -> float | None:
    if configuration.metric_type == "source":
        return _number(row.values[configuration.primary_kpi])
    metric = configuration.derived_metric
    if metric is None:
        return None
    return evaluate_derived_metric(metric, row.values).value


def _metric_group_value(row: _Row, configuration: BusinessConfiguration) -> float | None:
    metric = configuration.derived_metric
    if configuration.metric_type != "derived" or metric is None:
        return _metric_value(row, configuration)
    if metric.calculation_level == "row":
        return _metric_value(row, configuration)
    # Aggregate formulas use rows only when all referenced inputs are numeric.
    # The actual formula and division checks run once over each analysis group.
    return (
        0.0
        if all(_number(row.values[column]) is not None for column in metric.source_columns)
        else None
    )


def _aggregate_metric(
    entries: tuple[tuple[_Row, float], ...], configuration: BusinessConfiguration
) -> float | None:
    if not entries:
        return None
    metric = configuration.derived_metric
    aggregation = _metric_aggregation(configuration)
    if metric is not None:
        return aggregate_derived_metric(
            metric,
            tuple(row.values for row, _ in entries),
        ).value
    if aggregation == "sum":
        return _clean(math.fsum(value for _, value in entries))
    if aggregation == "mean":
        return _clean(math.fsum(value for _, value in entries) / len(entries))
    return None


def _metric_aggregation(configuration: BusinessConfiguration) -> str:
    metric = configuration.derived_metric
    return metric.aggregation if metric is not None else "sum"


def _aggregation_limitation(configuration: BusinessConfiguration, scope: str) -> str:
    aggregation = _metric_aggregation(configuration)
    if aggregation == "ratio_of_sums":
        return f"The derived KPI is calculated as a ratio of source-column sums per {scope}."
    if aggregation == "formula":
        return f"The aggregate formula is evaluated from source-column aggregates per {scope}."
    return f"Valid KPI values use {aggregation} aggregation per {scope}."


def _metric_definition(configuration: BusinessConfiguration) -> dict[str, object]:
    metric = configuration.derived_metric
    if configuration.metric_type == "derived" and metric is not None:
        return {
            "metric_id": configuration.primary_metric_id,
            "metric_type": "derived",
            "name": metric.name,
            "formula": metric.formula_label,
            "source_columns": list(metric.source_columns),
            "operation": metric.operation,
            "aggregation": metric.aggregation,
            "display_format": metric.display_format,
            "division_by_zero": "return_null",
            "missing_input": "return_null",
        }
    return {
        "metric_id": configuration.primary_metric_id,
        "metric_type": "source",
        "name": configuration.primary_kpi,
        "source_columns": [configuration.primary_kpi],
        "aggregation": "sum",
    }


def _is_missing(value: str) -> bool:
    return value.strip().casefold() in _MISSING_MARKERS


def _number(value: str) -> float | None:
    if _is_missing(value):
        return None
    try:
        number = float(value.strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _parse_datetime(value: str) -> datetime | None:
    candidate = value.strip()
    if not any(separator in candidate for separator in ("-", "/", ":", "T", " ")):
        return None
    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(candidate, "%Y/%m/%d")
        except ValueError:
            return None


def _period_label(value: datetime, granularity: str) -> str:
    normalized = value
    if value.tzinfo is not None and value.utcoffset() is not None:
        normalized = value.astimezone(UTC)
    return normalized.strftime("%Y-%m" if granularity == "month" else "%Y-%m-%d")


def _direction(value: float) -> str:
    if math.isclose(value, 0.0, abs_tol=1e-12):
        return "stable"
    return "increasing" if value > 0 else "decreasing"


def _favorable(direction: str, kpi_direction: str) -> bool | None:
    if direction == "stable":
        return None
    return (direction == "increasing") == (kpi_direction == "higher")


def _linear_trend(values: list[float]) -> tuple[float, float]:
    count = len(values)
    x_mean = (count - 1) / 2
    y_mean = math.fsum(values) / count
    numerator = math.fsum((index - x_mean) * (value - y_mean) for index, value in enumerate(values))
    x_variance = math.fsum((index - x_mean) ** 2 for index in range(count))
    y_variance = math.fsum((value - y_mean) ** 2 for value in values)
    slope = numerator / x_variance
    r_squared = 0.0 if y_variance == 0 else (numerator**2) / (x_variance * y_variance)
    return slope, r_squared


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    count = len(pairs)
    left_mean = math.fsum(left for left, _ in pairs) / count
    right_mean = math.fsum(right for _, right in pairs) / count
    numerator = math.fsum(
        (left - left_mean) * (right - right_mean) for left, right in pairs
    )
    left_variance = math.fsum((left - left_mean) ** 2 for left, _ in pairs)
    right_variance = math.fsum((right - right_mean) ** 2 for _, right in pairs)
    denominator = math.sqrt(left_variance * right_variance)
    if denominator == 0:
        return None
    return max(-1.0, min(1.0, numerator / denominator))


def _correlation_strength(coefficient: float) -> str:
    magnitude = abs(coefficient)
    if magnitude >= 0.7:
        return "strong"
    if magnitude >= 0.4:
        return "moderate"
    return "weak"


def _count_confidence(count: int) -> str:
    if count >= 30:
        return "high"
    if count >= 10:
        return "medium"
    return "low"


def _clean(value: float) -> float:
    rounded = round(float(value), 10)
    return 0.0 if rounded == 0 else rounded
