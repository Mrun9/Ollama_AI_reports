"""Reproducible, Python-only factual insight calculations."""

import json
import math
import secrets
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from insight_reporter.business_config import BusinessConfiguration
from insight_reporter.conditional_metrics import evaluate_conditional_metric
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
from insight_reporter.formula_engine import aggregate_row_values

_MISSING_MARKERS = frozenset({"", "na", "n/a", "null", "none", "nan"})
_MIN_GENERAL_RECORDS = 5
_MIN_PERIOD_RECORDS = 2
_MIN_TREND_PERIODS = 3
_MIN_SEGMENT_RECORDS = 2
_MAX_BASELINE_PERIODS = 4
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


@dataclass(frozen=True)
class _MetricCapabilities:
    row_level_analysis: bool
    additive: bool
    applicable_analyses: tuple[str, ...]
    not_applicable_analyses: tuple[dict[str, str], ...]


class _Collector:
    def __init__(self) -> None:
        self.insights: list[Insight] = []
        self.metric_id = "DATASET"
        self._insufficient_issues: list[dict[str, object]] = []

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

    def add_insufficient_issue(
        self,
        *,
        analysis: str,
        available: int,
        required: int,
        unit: str,
        recommendation: str,
    ) -> None:
        self._insufficient_issues.append(
            {
                "analysis": analysis,
                "status": "insufficient_data",
                "available": available,
                "required": required,
                "unit": unit,
                "recommendation": recommendation,
            }
        )

    def flush_insufficient_issues(
        self,
        *,
        metric: str,
        source_columns: tuple[str, ...],
    ) -> None:
        if not self._insufficient_issues:
            return
        issues = list(self._insufficient_issues)
        self._insufficient_issues.clear()
        self.add(
            insight_type="insufficient_data_warning",
            metric=metric,
            observation={
                "reason": "analysis_requirements_not_met",
                "issue_count": len(issues),
                "issues": issues,
            },
            source_columns=source_columns,
            record_count=max(
                (int(issue["available"]) for issue in issues),
                default=0,
            ),
            limitations=(
                "Unavailable analyses are consolidated into one diagnostic "
                "record and are not management findings.",
            ),
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
        rows=rows,
    )

    collector = _Collector()
    _add_missing_data_insights(collector, profile)
    for metric in configuration.metrics:
        metric_configuration = configuration.for_metric(metric.metric_id)
        collector.metric_id = metric.metric_id
        capabilities = _metric_capabilities(metric_configuration)
        _add_metric_snapshot(
            collector,
            rows=rows,
            configuration=metric_configuration,
            capabilities=capabilities,
        )
        if len(rows) < _MIN_GENERAL_RECORDS:
            _add_insufficient_warning(
                collector,
                metric=metric_configuration.primary_kpi,
                analysis="dataset_size",
                available=len(rows),
                required=_MIN_GENERAL_RECORDS,
                unit="valid_records",
                recommendation=(
                    "Provide at least five source records before treating "
                    "dataset-level patterns as stable."
                ),
                source_columns=_metric_source_columns(metric_configuration),
            )

        temporal = _prepare_temporal_context(
            collector,
            rows=rows,
            configuration=metric_configuration,
        )
        if temporal is not None:
            _add_period_change(collector, temporal, metric_configuration)
            _add_period_baseline_comparison(
                collector,
                temporal,
                metric_configuration,
            )
            _add_trend(collector, temporal, metric_configuration)
            _add_period_target_comparison(
                collector,
                temporal,
                metric_configuration,
            )

        for category_column in metric_configuration.category_columns:
            _add_segment_ranking(
                collector,
                rows=rows,
                category_column=category_column,
                configuration=metric_configuration,
            )
            _add_segment_share(
                collector,
                rows=rows,
                category_column=category_column,
                configuration=metric_configuration,
            )
            _add_segment_benchmark_performance(
                collector,
                rows=rows,
                category_column=category_column,
                configuration=metric_configuration,
            )
            _add_segment_target_comparison(
                collector,
                rows=rows,
                category_column=category_column,
                configuration=metric_configuration,
            )
            if temporal is not None:
                _add_cohort_period_comparison(
                    collector,
                    temporal=temporal,
                    category_column=category_column,
                    configuration=metric_configuration,
                )
                _add_segment_contribution(
                    collector,
                    temporal=temporal,
                    category_column=category_column,
                    configuration=metric_configuration,
                )

        if capabilities.row_level_analysis:
            _add_anomalies(
                collector, rows=rows, configuration=metric_configuration
            )
            _add_correlations(
                collector,
                rows=rows,
                profile=profile,
                configuration=metric_configuration,
            )
        if (
            capabilities.row_level_analysis
            and metric_configuration.metric_type != "conditional_rate"
            and metric_configuration.target_scope == "row"
        ):
            _add_benchmark_breaches(
                collector, rows=rows, configuration=metric_configuration
            )
        collector.flush_insufficient_issues(
            metric=metric_configuration.primary_kpi,
            source_columns=_metric_source_columns(metric_configuration),
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
    rows: tuple[_Row, ...],
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
        elif metric_configuration.metric_type == "conditional_rate":
            conditional = metric_configuration.conditional_metric
            if conditional is None:
                raise InsightEngineError(
                    "Conditional KPI configuration has no definition."
                )
            available_values = {
                row.values[conditional.condition_column].strip()
                for row in rows
            }
            if not set(conditional.included_values).issubset(
                available_values
            ):
                raise InsightEngineError(
                    "Conditional KPI values no longer exist in the retained dataset."
                )
        else:
            raise InsightEngineError("Configured KPI type is unsupported.")
        selected_columns.update(_metric_source_columns(metric_configuration))
    if configuration.date_column is not None:
        selected_columns.add(configuration.date_column)
    if not selected_columns.issubset(headers):
        raise InsightEngineError("Configured analysis columns are missing from the CSV.")


def _add_missing_data_insights(collector: _Collector, profile: DatasetProfile) -> None:
    affected = [
        {
            "column": column.name,
            "missing_count": column.missing_count,
            "missing_percentage": _clean(column.missing_percentage),
            "total_records": profile.row_count,
        }
        for column in profile.columns
        if column.missing_count > 0
    ]
    if not affected:
        return
    collector.add(
        insight_type="missing_data_warning",
        metric="Dataset completeness",
        observation={
            "affected_column_count": len(affected),
            "total_column_count": len(profile.columns),
            "maximum_missing_percentage": max(
                float(item["missing_percentage"])
                for item in affected
            ),
            "columns": affected,
        },
        source_columns=tuple(str(item["column"]) for item in affected),
        record_count=profile.row_count,
        limitations=(
            "Configured missing-value markers are treated as missing.",
            "Column-level missingness is consolidated into one dataset diagnostic.",
        ),
    )


def _metric_capabilities(
    configuration: BusinessConfiguration,
) -> _MetricCapabilities:
    derived = configuration.derived_metric
    row_level = (
        configuration.metric_type == "source"
        or (
            configuration.metric_type == "derived"
            and derived is not None
            and derived.calculation_level == "row"
        )
    )
    additive = _metric_aggregation(configuration) == "sum"
    applicable = ["metric_snapshot"]
    excluded: list[dict[str, str]] = []
    if configuration.date_column is not None:
        applicable.extend(
            ("period_change", "period_baseline_comparison", "trend")
        )
    else:
        excluded.append(
            {
                "analysis": "temporal_analyses",
                "reason": "requires_confirmed_date_column",
            }
        )
    if configuration.category_columns:
        applicable.append("segment_ranking")
        if configuration.date_column is not None:
            applicable.append("cohort_period_comparison")
    else:
        excluded.append(
            {
                "analysis": "segment_analyses",
                "reason": "requires_confirmed_category_column",
            }
        )
    if row_level:
        applicable.extend(
            (
                "iqr_anomaly_detection",
                "numeric_correlation",
            )
        )
    else:
        excluded.extend(
            (
                {
                    "analysis": "iqr_anomaly_detection",
                    "reason": "requires_row_level_kpi_values",
                },
                {
                    "analysis": "numeric_correlation",
                    "reason": "requires_row_level_kpi_values",
                },
                {
                    "analysis": "benchmark_breach",
                    "reason": "requires_row_level_kpi_values",
                },
            )
        )
    if additive:
        if configuration.category_columns:
            applicable.append("segment_share")
            if configuration.date_column is not None:
                applicable.append("segment_contribution")
    else:
        excluded.extend(
            (
                {
                    "analysis": "segment_share",
                    "reason": "requires_nonnegative_additive_sum_values",
                },
                {
                    "analysis": "segment_contribution",
                    "reason": "requires_additive_sum_metric",
                },
            )
        )
    if configuration.target_or_benchmark is not None:
        target_scope = configuration.target_scope
        if target_scope == "dataset":
            applicable.append("dataset_target_comparison")
        elif target_scope == "period":
            applicable.append("period_target_comparison")
        elif target_scope == "segment":
            applicable.append("segment_target_comparison")
        elif row_level:
            applicable.append("row_benchmark_breach")
    return _MetricCapabilities(
        row_level_analysis=row_level,
        additive=additive,
        applicable_analyses=tuple(dict.fromkeys(applicable)),
        not_applicable_analyses=tuple(excluded),
    )


def _add_metric_snapshot(
    collector: _Collector,
    *,
    rows: tuple[_Row, ...],
    configuration: BusinessConfiguration,
    capabilities: _MetricCapabilities,
) -> None:
    """Create one decision anchor and disclose the KPI's analysis coverage."""

    entries = tuple(
        (row, value)
        for row in rows
        if (value := _metric_group_value(row, configuration)) is not None
    )
    current_value = _aggregate_metric(entries, configuration)
    if current_value is None:
        _add_insufficient_warning(
            collector,
            metric=configuration.primary_kpi,
            analysis="metric_snapshot",
            available=len(entries),
            required=1,
            unit="valid_records",
            recommendation=(
                "Provide at least one record with every source value required "
                "by this KPI definition."
            ),
            source_columns=_metric_source_columns(configuration),
        )
        return
    observation: dict[str, object] = {
        "current_value": _clean(current_value),
        "aggregation": _metric_aggregation(configuration),
        "display_format": configuration.primary_metric.display_format,
        "valid_record_count": len(entries),
        "excluded_record_count": len(rows) - len(entries),
        "total_record_count": len(rows),
        "applicable_analyses": list(capabilities.applicable_analyses),
        "not_applicable_analyses": list(
            capabilities.not_applicable_analyses
        ),
    }
    limitations = [
        _aggregation_limitation(configuration, "complete dataset"),
        (
            "This is a descriptive whole-dataset snapshot; period and segment "
            "findings are calculated separately."
        ),
    ]
    conditional = configuration.conditional_metric
    target = configuration.target_or_benchmark
    observation["target_scope"] = configuration.target_scope
    if conditional is not None:
        evaluation = evaluate_conditional_metric(
            conditional,
            tuple(row.values for row, _value in entries),
        )
        observation.update(
            {
                "numerator": evaluation.numerator,
                "denominator": evaluation.denominator,
                "numerator_record_count": (
                    evaluation.numerator_record_count
                ),
                "denominator_record_count": (
                    evaluation.denominator_record_count
                ),
            }
        )
    if target is not None:
        observation["target"] = _clean(target)
        observation["kpi_direction"] = configuration.kpi_direction
        if configuration.target_scope == "dataset":
            meets_target = _meets_target(
                current_value,
                target,
                configuration.kpi_direction,
            )
            observation.update(
                {
                    "gap_to_target": _clean(current_value - target),
                    "meets_target": meets_target,
                    "favorable": meets_target,
                }
            )
            limitations.append(
                "The user-provided target is compared with the complete "
                "dataset KPI aggregate."
            )
        else:
            limitations.append(
                f"The user-provided target applies per "
                f"{configuration.target_scope}; its comparisons are calculated "
                "separately from this whole-dataset snapshot."
            )
    collector.add(
        insight_type="metric_snapshot",
        metric=configuration.primary_kpi,
        observation=observation,
        source_columns=_metric_source_columns(configuration),
        record_count=len(entries),
        confidence=_count_confidence(len(entries)),
        limitations=tuple(limitations),
    )


def _prepare_temporal_context(
    collector: _Collector,
    *,
    rows: tuple[_Row, ...],
    configuration: BusinessConfiguration,
) -> _TemporalContext | None:
    date_column = configuration.date_column
    metric = configuration.primary_kpi
    temporal_types = [
        "period_change",
        "period_baseline_comparison",
        "trend",
        "cohort_period_comparison",
        "segment_contribution",
    ]
    if (
        configuration.target_or_benchmark is not None
        and configuration.target_scope == "period"
    ):
        temporal_types.append("period_target_comparison")
    if date_column is None:
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
    if len(distinct_months) >= 24:
        granularity = "year"
    elif len(distinct_months) >= 6:
        granularity = "quarter"
    elif len(distinct_months) >= 2:
        granularity = "month"
    else:
        granularity = "day"
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
            unit="eligible_periods",
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
            unit="valid_records_per_period",
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


def _add_period_baseline_comparison(
    collector: _Collector,
    temporal: _TemporalContext,
    configuration: BusinessConfiguration,
) -> None:
    """Compare the latest eligible period with recent period-level history."""

    metric = configuration.primary_kpi
    date_column = configuration.date_column
    assert date_column is not None
    eligible = [
        (period, temporal.groups[period])
        for period in sorted(temporal.groups)
        if len(temporal.groups[period]) >= _MIN_PERIOD_RECORDS
    ]
    if len(eligible) < 3:
        _add_insufficient_warning(
            collector,
            metric=metric,
            analysis="period_baseline_comparison",
            available=max(0, len(eligible) - 1),
            required=2,
            unit="prior_eligible_periods",
            source_columns=_source_columns(configuration, date_column),
        )
        return

    current_period, current_rows = eligible[-1]
    baseline_groups = eligible[:-1][-_MAX_BASELINE_PERIODS:]
    period_values: list[dict[str, object]] = []
    baseline_values: list[float] = []
    for period, rows in baseline_groups:
        value = _aggregate_metric(rows, configuration)
        if value is None:
            collector.add(
                insight_type="analysis_skipped",
                metric=metric,
                observation={
                    "reason": "undefined_derived_aggregate",
                    "analysis": "period_baseline_comparison",
                },
                source_columns=_source_columns(
                    configuration,
                    date_column,
                ),
                record_count=sum(
                    len(group_rows)
                    for _, group_rows in (*baseline_groups, eligible[-1])
                ),
                limitations=(
                    "The configured derived aggregation produced no "
                    "finite baseline-period value.",
                ),
            )
            return
        baseline_values.append(value)
        period_values.append(
            {
                "period": period,
                "value": _clean(value),
                "role": "baseline",
                "record_count": len(rows),
            }
        )

    current_value = _aggregate_metric(current_rows, configuration)
    if current_value is None:
        collector.add(
            insight_type="analysis_skipped",
            metric=metric,
            observation={
                "reason": "undefined_derived_aggregate",
                "analysis": "period_baseline_comparison",
            },
            source_columns=_source_columns(configuration, date_column),
            record_count=sum(
                len(rows)
                for _, rows in (*baseline_groups, eligible[-1])
            ),
            limitations=(
                "The configured derived aggregation produced no finite "
                "current-period value.",
            ),
        )
        return

    baseline_value = statistics.fmean(baseline_values)
    absolute_change = current_value - baseline_value
    limitations = [
        _aggregation_limitation(configuration, "calendar period"),
        (
            "The baseline is the arithmetic mean of up to four immediately "
            "preceding eligible period aggregates; it is descriptive, not a "
            "forecast or seasonal adjustment."
        ),
    ]
    if math.isclose(baseline_value, 0.0, abs_tol=1e-12):
        percentage_change = None
        limitations.append(
            "Percentage difference is not calculated because the baseline "
            "value is zero."
        )
    else:
        percentage_change = _clean(
            (absolute_change / baseline_value) * 100
        )
    period_values.append(
        {
            "period": current_period,
            "value": _clean(current_value),
            "role": "current",
            "record_count": len(current_rows),
        }
    )
    direction = _direction(absolute_change)
    baseline_periods = [period for period, _ in baseline_groups]
    collector.add(
        insight_type="period_baseline_comparison",
        metric=metric,
        observation={
            "aggregation": _metric_aggregation(configuration),
            "period_granularity": temporal.granularity,
            "baseline_method": "mean_of_prior_period_aggregates",
            "baseline_periods": baseline_periods,
            "baseline_period_count": len(baseline_values),
            "baseline_value": _clean(baseline_value),
            "baseline_minimum": _clean(min(baseline_values)),
            "baseline_maximum": _clean(max(baseline_values)),
            "current_period": current_period,
            "current_value": _clean(current_value),
            "absolute_change": _clean(absolute_change),
            "percentage_change": percentage_change,
            "direction": direction,
            "favorable": _favorable(
                direction,
                configuration.kpi_direction,
            ),
            "period_values": period_values,
        },
        source_columns=_source_columns(configuration, date_column),
        filters={"periods": [*baseline_periods, current_period]},
        record_count=sum(
            len(rows) for _, rows in (*baseline_groups, eligible[-1])
        ),
        confidence=_count_confidence(
            min(
                len(rows)
                for _, rows in (*baseline_groups, eligible[-1])
            )
        ),
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
            unit="eligible_periods",
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
            unit="eligible_segments",
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
            unit="eligible_segments",
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


def _add_segment_share(
    collector: _Collector,
    *,
    rows: tuple[_Row, ...],
    category_column: str,
    configuration: BusinessConfiguration,
) -> None:
    """Calculate each named category's reconciled share of an additive KPI."""

    if _metric_aggregation(configuration) != "sum":
        return
    groups: dict[str, list[tuple[_Row, float]]] = {}
    missing_category_count = 0
    for row in rows:
        segment = row.values[category_column].strip()
        number = _metric_group_value(row, configuration)
        if not segment or _is_missing(segment):
            missing_category_count += 1
            continue
        if number is not None:
            groups.setdefault(segment, []).append((row, number))
    if len(groups) < 2:
        return
    segment_values: list[dict[str, object]] = []
    for segment, entries in groups.items():
        value = _aggregate_metric(tuple(entries), configuration)
        if value is None:
            continue
        segment_values.append(
            {
                "segment": segment,
                "value": float(value),
                "record_count": len(entries),
            }
        )
    if len(segment_values) < 2:
        return
    values = [float(item["value"]) for item in segment_values]
    total = math.fsum(values)
    if total <= 0 or any(value < 0 for value in values):
        collector.add(
            insight_type="analysis_skipped",
            metric=configuration.primary_kpi,
            observation={
                "reason": "category_share_requires_nonnegative_additive_values",
                "analysis": f"segment_share:{category_column}",
                "category_column": category_column,
            },
            source_columns=_source_columns(configuration, category_column),
            record_count=sum(
                int(item["record_count"]) for item in segment_values
            ),
            limitations=(
                "Category shares require a positive total and non-negative "
                "additive segment values.",
            ),
        )
        return
    segment_values.sort(
        key=lambda item: (
            -float(item["value"]),
            str(item["segment"]).casefold(),
        )
    )
    for item in segment_values:
        item["share_percentage"] = _clean(
            (float(item["value"]) / total) * 100
        )
    rounded_total = math.fsum(
        float(item["share_percentage"]) for item in segment_values
    )
    segment_values[-1]["share_percentage"] = _clean(
        float(segment_values[-1]["share_percentage"])
        + (100.0 - rounded_total)
    )
    collector.add(
        insight_type="segment_share",
        metric=configuration.primary_kpi,
        observation={
            "aggregation": "sum",
            "category_column": category_column,
            "total_value": _clean(total),
            "reconciled_percentage_total": 100.0,
            "missing_category_count": missing_category_count,
            "top_segment": segment_values[0],
            "bottom_segment": segment_values[-1],
            "shares": segment_values,
        },
        source_columns=_source_columns(configuration, category_column),
        record_count=sum(
            int(item["record_count"]) for item in segment_values
        ),
        confidence=_count_confidence(
            sum(int(item["record_count"]) for item in segment_values)
        ),
        limitations=(
            "Shares use the sum of valid KPI values with non-missing category labels.",
            "Shares describe composition and do not explain why the mix occurred.",
        ),
    )


def _add_cohort_period_comparison(
    collector: _Collector,
    *,
    temporal: _TemporalContext,
    category_column: str,
    configuration: BusinessConfiguration,
) -> None:
    """Compare like-for-like category cohorts across the latest two periods."""

    periods = sorted(temporal.groups)
    if len(periods) < 2:
        return
    previous_period, current_period = periods[-2:]
    previous_rows = temporal.groups[previous_period]
    current_rows = temporal.groups[current_period]
    metric = configuration.primary_kpi
    date_column = configuration.date_column
    assert date_column is not None

    def cohort_groups(
        entries: tuple[tuple[_Row, float], ...],
    ) -> dict[str, tuple[tuple[_Row, float], ...]]:
        groups: dict[str, list[tuple[_Row, float]]] = {}
        for row, number in entries:
            cohort = row.values[category_column].strip()
            if cohort and not _is_missing(cohort):
                groups.setdefault(cohort, []).append((row, number))
        return {
            cohort: tuple(values)
            for cohort, values in groups.items()
        }

    previous = cohort_groups(previous_rows)
    current = cohort_groups(current_rows)
    shared_cohorts = sorted(
        set(previous) & set(current),
        key=str.casefold,
    )
    eligible_cohorts = [
        cohort
        for cohort in shared_cohorts
        if min(len(previous[cohort]), len(current[cohort]))
        >= _MIN_SEGMENT_RECORDS
    ]
    if len(eligible_cohorts) < 2:
        _add_insufficient_warning(
            collector,
            metric=metric,
            analysis=f"cohort_period_comparison:{category_column}",
            available=len(eligible_cohorts),
            required=2,
            unit="eligible_cohorts",
            source_columns=_source_columns(
                configuration,
                date_column,
                category_column,
            ),
        )
        return

    comparisons_with_scores: list[
        tuple[dict[str, object], float]
    ] = []
    for cohort in eligible_cohorts:
        previous_value = _aggregate_metric(
            previous[cohort],
            configuration,
        )
        current_value = _aggregate_metric(
            current[cohort],
            configuration,
        )
        if previous_value is None or current_value is None:
            continue
        absolute_change = current_value - previous_value
        if math.isclose(previous_value, 0.0, abs_tol=1e-12):
            percentage_change = None
            magnitude = (
                abs(absolute_change)
                / max(abs(current_value), 1.0)
            ) * 100
        else:
            percentage_change = _clean(
                (absolute_change / previous_value) * 100
            )
            magnitude = abs(percentage_change)
        direction = _direction(absolute_change)
        direction_sign = 1.0 if absolute_change >= 0 else -1.0
        if configuration.kpi_direction == "lower":
            direction_sign *= -1
        directional_score = magnitude * direction_sign
        comparisons_with_scores.append(
            (
                {
                    "cohort": cohort,
                    "previous_value": _clean(previous_value),
                    "current_value": _clean(current_value),
                    "absolute_change": _clean(absolute_change),
                    "percentage_change": percentage_change,
                    "direction": direction,
                    "favorable": _favorable(
                        direction,
                        configuration.kpi_direction,
                    ),
                    "previous_record_count": len(previous[cohort]),
                    "current_record_count": len(current[cohort]),
                },
                directional_score,
            )
        )
    if len(comparisons_with_scores) < 2:
        _add_insufficient_warning(
            collector,
            metric=metric,
            analysis=f"cohort_period_comparison:{category_column}",
            available=len(comparisons_with_scores),
            required=2,
            unit="eligible_cohorts",
            source_columns=_source_columns(
                configuration,
                date_column,
                category_column,
            ),
        )
        return

    ordered = sorted(
        comparisons_with_scores,
        key=lambda item: (
            item[1],
            str(item[0]["cohort"]).casefold(),
        ),
    )
    comparisons = [
        item
        for item, _score in sorted(
            comparisons_with_scores,
            key=lambda pair: (
                -abs(pair[1]),
                str(pair[0]["cohort"]).casefold(),
            ),
        )
    ]
    included_cohorts = {
        str(item["cohort"]) for item in comparisons
    }
    excluded_cohort_count = len(
        (set(previous) | set(current)) - included_cohorts
    )
    collector.add(
        insight_type="cohort_period_comparison",
        metric=metric,
        observation={
            "aggregation": _metric_aggregation(configuration),
            "category_column": category_column,
            "period_granularity": temporal.granularity,
            "previous_period": previous_period,
            "current_period": current_period,
            "cohort_count": len(comparisons),
            "excluded_cohort_count": excluded_cohort_count,
            "best_performing_change": ordered[-1][0],
            "worst_performing_change": ordered[0][0],
            "comparisons": comparisons,
        },
        source_columns=_source_columns(
            configuration,
            date_column,
            category_column,
        ),
        filters={
            "periods": [previous_period, current_period],
            "cohort_column": category_column,
        },
        record_count=sum(
            len(previous[cohort]) + len(current[cohort])
            for cohort in included_cohorts
        ),
        confidence=_count_confidence(
            min(
                min(len(previous[cohort]), len(current[cohort]))
                for cohort in included_cohorts
            )
        ),
        limitations=(
            _aggregation_limitation(
                configuration,
                f"{category_column} cohort and calendar period",
            ),
            (
                "Only like-for-like cohorts with at least two valid KPI "
                "records in both comparison periods are included."
            ),
            (
                "Cohort changes are descriptive and do not establish why "
                "performance changed."
            ),
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


def _add_segment_benchmark_performance(
    collector: _Collector,
    *,
    rows: tuple[_Row, ...],
    category_column: str,
    configuration: BusinessConfiguration,
) -> None:
    """Compare confirmed row-level target attainment by business segment."""

    target = configuration.target_or_benchmark
    if target is None or configuration.target_scope != "row":
        return
    groups: dict[str, list[float]] = {}
    for row in rows:
        segment = row.values[category_column].strip()
        value = _metric_value(row, configuration)
        if segment and not _is_missing(segment) and value is not None:
            groups.setdefault(segment, []).append(value)
    eligible = {
        segment: values
        for segment, values in groups.items()
        if len(values) >= _MIN_SEGMENT_RECORDS
    }
    if len(eligible) < 2:
        return

    performance: list[dict[str, object]] = []
    for segment, values in eligible.items():
        breaches = [
            value
            for value in values
            if (
                value < target
                if configuration.kpi_direction == "higher"
                else value > target
            )
        ]
        average_value = math.fsum(values) / len(values)
        performance.append(
            {
                "segment": segment,
                "target": _clean(target),
                "average_value": _clean(average_value),
                "average_gap_to_target": _clean(average_value - target),
                "breach_count": len(breaches),
                "record_count": len(values),
                "breach_percentage": _clean(
                    (len(breaches) / len(values)) * 100
                ),
            }
        )
    performance.sort(
        key=lambda item: (
            -float(item["breach_percentage"]),
            str(item["segment"]).casefold(),
        )
    )
    collector.add(
        insight_type="segment_benchmark_performance",
        metric=configuration.primary_kpi,
        observation={
            "category_column": category_column,
            "target": _clean(target),
            "kpi_direction": configuration.kpi_direction,
            "worst_segment": performance[0],
            "best_segment": performance[-1],
            "segment_performance": performance,
        },
        source_columns=_source_columns(
            configuration,
            category_column,
        ),
        record_count=sum(len(values) for values in eligible.values()),
        confidence=_count_confidence(
            min(len(values) for values in eligible.values())
        ),
        limitations=(
            "Target attainment is evaluated per valid row within each segment.",
            "Segments with fewer than two valid KPI records are excluded.",
        ),
    )


def _add_period_target_comparison(
    collector: _Collector,
    temporal: _TemporalContext,
    configuration: BusinessConfiguration,
) -> None:
    """Compare each eligible period aggregate with a per-period target."""

    target = configuration.target_or_benchmark
    if target is None or configuration.target_scope != "period":
        return
    performance: list[dict[str, object]] = []
    for period, entries in sorted(temporal.groups.items()):
        if len(entries) < _MIN_PERIOD_RECORDS:
            continue
        value = _aggregate_metric(entries, configuration)
        if value is None:
            continue
        meets_target = _meets_target(
            value,
            target,
            configuration.kpi_direction,
        )
        performance.append(
            {
                "period": period,
                "value": _clean(value),
                "target": _clean(target),
                "gap_to_target": _clean(value - target),
                "meets_target": meets_target,
                "favorable": meets_target,
                "record_count": len(entries),
            }
        )
    if not performance:
        _add_insufficient_warning(
            collector,
            metric=configuration.primary_kpi,
            analysis="period_target_comparison",
            available=0,
            required=1,
            unit="eligible_periods",
            source_columns=_source_columns(
                configuration,
                configuration.date_column or "",
            ),
        )
        return
    current = performance[-1]
    collector.add(
        insight_type="period_target_comparison",
        metric=configuration.primary_kpi,
        observation={
            "target_scope": "period",
            "target": _clean(target),
            "kpi_direction": configuration.kpi_direction,
            "granularity": temporal.granularity,
            "current_period": current["period"],
            "current_value": current["value"],
            "current_gap_to_target": current["gap_to_target"],
            "current_meets_target": current["meets_target"],
            "missed_period_count": sum(
                not bool(item["meets_target"]) for item in performance
            ),
            "eligible_period_count": len(performance),
            "period_performance": performance,
        },
        source_columns=_source_columns(
            configuration,
            configuration.date_column or "",
        ),
        record_count=sum(int(item["record_count"]) for item in performance),
        confidence=_count_confidence(
            min(int(item["record_count"]) for item in performance)
        ),
        limitations=(
            _aggregation_limitation(configuration, "eligible period"),
            "Periods with fewer than two valid KPI records are excluded.",
        ),
    )


def _add_segment_target_comparison(
    collector: _Collector,
    *,
    rows: tuple[_Row, ...],
    category_column: str,
    configuration: BusinessConfiguration,
) -> None:
    """Compare each eligible segment aggregate with a per-segment target."""

    target = configuration.target_or_benchmark
    if target is None or configuration.target_scope != "segment":
        return
    groups: dict[str, list[tuple[_Row, float]]] = {}
    for row in rows:
        segment = row.values[category_column].strip()
        value = _metric_group_value(row, configuration)
        if segment and not _is_missing(segment) and value is not None:
            groups.setdefault(segment, []).append((row, value))
    performance: list[dict[str, object]] = []
    for segment, group_entries in groups.items():
        if len(group_entries) < _MIN_SEGMENT_RECORDS:
            continue
        entries = tuple(group_entries)
        value = _aggregate_metric(entries, configuration)
        if value is None:
            continue
        meets_target = _meets_target(
            value,
            target,
            configuration.kpi_direction,
        )
        performance.append(
            {
                "segment": segment,
                "value": _clean(value),
                "target": _clean(target),
                "gap_to_target": _clean(value - target),
                "meets_target": meets_target,
                "favorable": meets_target,
                "record_count": len(entries),
            }
        )
    if len(performance) < 2:
        _add_insufficient_warning(
            collector,
            metric=configuration.primary_kpi,
            analysis=f"segment_target_comparison:{category_column}",
            available=len(performance),
            required=2,
            unit="eligible_segments",
            source_columns=_source_columns(configuration, category_column),
        )
        return
    performance.sort(
        key=lambda item: (
            -_target_miss_amount(
                float(item["value"]),
                target,
                configuration.kpi_direction,
            ),
            (
                float(item["value"])
                if configuration.kpi_direction == "higher"
                else -float(item["value"])
            ),
            str(item["segment"]).casefold(),
        )
    )
    worst = performance[0]
    best = (
        max
        if configuration.kpi_direction == "higher"
        else min
    )(
        performance,
        key=lambda item: (
            float(item["value"]),
            str(item["segment"]).casefold(),
        ),
    )
    collector.add(
        insight_type="segment_target_comparison",
        metric=configuration.primary_kpi,
        observation={
            "target_scope": "segment",
            "category_column": category_column,
            "target": _clean(target),
            "kpi_direction": configuration.kpi_direction,
            "worst_segment": worst,
            "best_segment": best,
            "missed_segment_count": sum(
                not bool(item["meets_target"]) for item in performance
            ),
            "eligible_segment_count": len(performance),
            "segment_performance": performance,
        },
        source_columns=_source_columns(configuration, category_column),
        record_count=sum(int(item["record_count"]) for item in performance),
        confidence=_count_confidence(
            min(int(item["record_count"]) for item in performance)
        ),
        limitations=(
            _aggregation_limitation(configuration, "eligible segment"),
            "Segments with fewer than two valid KPI records are excluded.",
        ),
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
            unit="valid_row_values",
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
                unit="valid_numeric_pairs",
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
            unit="valid_row_values",
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
    unit: str = "eligible_records_or_groups",
    recommendation: str | None = None,
) -> None:
    del metric, source_columns
    collector.add_insufficient_issue(
        analysis=analysis,
        available=available,
        required=required,
        unit=unit,
        recommendation=(
            recommendation
            or _insufficient_recommendation(
                analysis,
                required=required,
                unit=unit,
            )
        ),
    )


def _insufficient_recommendation(
    analysis: str,
    *,
    required: int,
    unit: str,
) -> str:
    if analysis == "period_change":
        return (
            "Provide two eligible periods with at least two valid KPI records "
            "in each period."
        )
    if analysis == "period_baseline_comparison":
        return (
            "Provide a current eligible period and at least two prior eligible "
            "periods, each with at least two valid KPI records."
        )
    if analysis == "trend":
        return (
            "Provide at least three eligible periods, each with at least two "
            "valid KPI records."
        )
    if analysis.startswith("segment_ranking:"):
        return (
            "Provide at least two category values with two valid KPI records "
            "in each category."
        )
    if analysis.startswith("cohort_period_comparison:"):
        return (
            "Provide at least two category values that each have two valid KPI "
            "records in both latest periods; avoid using month or quarter "
            "labels as cohorts when a date column already defines periods."
        )
    if analysis.startswith("correlation:"):
        return (
            "Provide at least three rows where both the KPI and comparison "
            "column have valid row-level numeric values."
        )
    if analysis == "iqr_anomaly_detection":
        return "Provide at least four valid row-level KPI values."
    if analysis == "benchmark_breach":
        return "Provide at least three valid row-level KPI values."
    if analysis == "period_target_comparison":
        return (
            "Provide at least one period with two valid KPI records and keep "
            "the configured date column."
        )
    if analysis.startswith("segment_target_comparison:"):
        return (
            "Provide at least two category values with two valid KPI records "
            "in each category."
        )
    return (
        f"Provide at least {required} {unit.replace('_', ' ')} required by "
        "this analysis."
    )


def _metric_source_columns(configuration: BusinessConfiguration) -> tuple[str, ...]:
    if (
        configuration.metric_type == "conditional_rate"
        and configuration.conditional_metric is not None
    ):
        return configuration.conditional_metric.source_columns
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
    if configuration.metric_type == "conditional_rate":
        metric = configuration.conditional_metric
        if metric is None or metric.calculation_base != "record_count":
            return None
        return (
            100.0
            if row.values[metric.condition_column].strip()
            in set(metric.included_values)
            else 0.0
        )
    metric = configuration.derived_metric
    if metric is None:
        return None
    return evaluate_derived_metric(metric, row.values).value


def _metric_group_value(row: _Row, configuration: BusinessConfiguration) -> float | None:
    if configuration.metric_type == "conditional_rate":
        metric = configuration.conditional_metric
        if metric is None:
            return None
        if metric.calculation_base == "record_count":
            return 1.0
        return _number(row.values[metric.value_column or ""])
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
    if configuration.metric_type == "conditional_rate":
        conditional = configuration.conditional_metric
        if conditional is None:
            return None
        return evaluate_conditional_metric(
            conditional,
            tuple(row.values for row, _ in entries),
        ).percentage
    if metric is not None:
        return aggregate_derived_metric(
            metric,
            tuple(row.values for row, _ in entries),
        ).value
    return aggregate_row_values(
        [value for _, value in entries],
        aggregation,
    )


def _metric_aggregation(configuration: BusinessConfiguration) -> str:
    return configuration.primary_metric.aggregation


def _aggregation_limitation(configuration: BusinessConfiguration, scope: str) -> str:
    aggregation = _metric_aggregation(configuration)
    if aggregation == "conditional_rate":
        metric = configuration.conditional_metric
        base = (
            "record counts"
            if metric is not None
            and metric.calculation_base == "record_count"
            else "valid value sums"
        )
        return (
            f"The conditional percentage divides matching {base} by all "
            f"eligible {base} per {scope}."
        )
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
            "target_or_benchmark": configuration.target_or_benchmark,
            "target_scope": configuration.target_scope,
            "division_by_zero": "return_null",
            "missing_input": "return_null",
        }
    if (
        configuration.metric_type == "conditional_rate"
        and configuration.conditional_metric is not None
    ):
        conditional = configuration.conditional_metric
        return {
            "metric_id": configuration.primary_metric_id,
            "metric_type": "conditional_rate",
            "name": conditional.name,
            "formula": conditional.formula_label,
            "source_columns": list(conditional.source_columns),
            "aggregation": "conditional_rate",
            "display_format": "percentage",
            "calculation_base": conditional.calculation_base,
            "condition_column": conditional.condition_column,
            "included_values": list(conditional.included_values),
            "value_column": conditional.value_column,
            "zero_denominator": "return_null",
            "target_or_benchmark": configuration.target_or_benchmark,
            "target_scope": configuration.target_scope,
        }
    return {
        "metric_id": configuration.primary_metric_id,
        "metric_type": "source",
        "name": configuration.primary_kpi,
        "source_columns": [configuration.primary_kpi],
        "aggregation": configuration.primary_metric.aggregation,
        "display_format": configuration.primary_metric.display_format,
        "target_or_benchmark": configuration.target_or_benchmark,
        "target_scope": configuration.target_scope,
    }


def _meets_target(value: float, target: float, direction: str) -> bool:
    return value >= target if direction == "higher" else value <= target


def _target_miss_amount(value: float, target: float, direction: str) -> float:
    """Return a positive shortfall/excess when a value misses its target."""

    return max(target - value, 0.0) if direction == "higher" else max(
        value - target,
        0.0,
    )


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
    if granularity == "year":
        return normalized.strftime("%Y")
    if granularity == "quarter":
        quarter = ((normalized.month - 1) // 3) + 1
        return f"{normalized.year}-Q{quarter}"
    if granularity == "month":
        return normalized.strftime("%Y-%m")
    return normalized.strftime("%Y-%m-%d")


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
