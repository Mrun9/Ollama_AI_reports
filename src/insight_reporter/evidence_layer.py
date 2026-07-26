"""Traceable evidence records and secure Python charts for deterministic insights."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from insight_reporter.business_config import (
    BusinessConfiguration,
    MetricConfiguration,
)
from insight_reporter.dataset_profile import DatasetProfile
from insight_reporter.dataset_view import DatasetView
from insight_reporter.derived_metrics import evaluate_derived_metric
from insight_reporter.insight_engine import Insight, InsightReport

_MISSING_MARKERS = frozenset({"", "na", "n/a", "null", "none", "nan"})
_CHART_FILENAME = re.compile(r"[0-9a-f]{32}\.png")
_DATASET_ID = re.compile(r"[0-9a-f]{32}")
_EVIDENCE_ID = re.compile(r"EVD-[0-9A-F]{16}")
_MAX_LABEL_LENGTH = 60


class EvidenceError(ValueError):
    """Raised when an evidence artifact cannot be generated or loaded safely."""


@dataclass(frozen=True)
class EvidenceRanking:
    """Independent ranking signals and their reproducible combined score."""

    impact: float
    confidence: float
    relevance: float
    combined: float
    rank: int

    def to_dict(self) -> dict[str, object]:
        return {
            "impact": self.impact,
            "confidence": self.confidence,
            "relevance": self.relevance,
            "combined": self.combined,
            "rank": self.rank,
        }


@dataclass(frozen=True)
class ChartArtifact:
    """Metadata for one chart stored outside Flask's static directory."""

    filename: str
    chart_type: str
    title: str
    alt_text: str
    data_columns: tuple[str, ...]
    record_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "chart_type": self.chart_type,
            "title": self.title,
            "alt_text": self.alt_text,
            "data_columns": list(self.data_columns),
            "record_count": self.record_count,
        }


@dataclass(frozen=True)
class EvidenceRecord:
    """Reviewer-facing evidence tied one-to-one to a deterministic insight."""

    id: str
    insight_id: str
    insight_type: str
    metric_id: str
    metric: str
    kpi_definition: dict[str, object]
    source: dict[str, object]
    source_columns: tuple[str, ...]
    filters: dict[str, object]
    periods: tuple[str, ...]
    calculation_description: str
    observation: dict[str, object]
    supporting_data: tuple[dict[str, object], ...]
    record_count: int
    ranking: EvidenceRanking
    chart: ChartArtifact | None
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "insight_id": self.insight_id,
            "insight_type": self.insight_type,
            "metric_id": self.metric_id,
            "metric": self.metric,
            "kpi_definition": self.kpi_definition,
            "source": self.source,
            "source_columns": list(self.source_columns),
            "filters": self.filters,
            "periods": list(self.periods),
            "calculation_description": self.calculation_description,
            "observation": self.observation,
            "supporting_data": list(self.supporting_data),
            "record_count": self.record_count,
            "ranking": self.ranking.to_dict(),
            "chart": self.chart.to_dict() if self.chart is not None else None,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class EvidenceReport:
    """Versioned evidence artifact for one immutable dataset."""

    schema_version: int
    dataset_id: str
    sources: tuple[dict[str, object], ...]
    records: tuple[EvidenceRecord, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "sources": list(self.sources),
            "records": [record.to_dict() for record in self.records],
        }


def generate_evidence(
    view: DatasetView,
    *,
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
    insight_report: InsightReport,
    chart_dir: Path,
) -> EvidenceReport:
    """Build one evidence record per deterministic insight and generate charts."""

    _validate_inputs(view, profile, configuration, insight_report)
    chart_dir = _secure_directory(chart_dir)
    created_files: list[Path] = []
    records: list[EvidenceRecord] = []
    missing_overview_created = False
    try:
        for insight in insight_report.insights:
            metric_configuration = _metric_configuration(configuration, insight)
            supporting_data = _supporting_data(
                insight,
                view=view,
                profile=profile,
                metric_configuration=metric_configuration,
            )
            chart_type = _chart_type_for(
                insight,
                missing_overview_created=missing_overview_created,
            )
            chart = None
            if chart_type is not None:
                chart = _generate_chart(
                    chart_type,
                    insight=insight,
                    supporting_data=supporting_data,
                    chart_dir=chart_dir,
                )
                if chart is not None:
                    created_files.append(_chart_path(chart_dir, chart.filename))
                    if chart_type == "missing_data_overview":
                        missing_overview_created = True

            record = EvidenceRecord(
                id=_evidence_id(insight_report.dataset_id, insight),
                insight_id=insight.id,
                insight_type=insight.type,
                metric_id=insight.metric_id,
                metric=insight.metric,
                kpi_definition=_kpi_definition(metric_configuration, insight),
                source=_source_metadata(view),
                source_columns=insight.source_columns,
                filters=dict(insight.filters),
                periods=_periods(insight),
                calculation_description=_calculation_description(insight),
                observation=dict(insight.observation),
                supporting_data=supporting_data,
                record_count=insight.record_count,
                ranking=_ranking(insight),
                chart=chart,
                limitations=insight.limitations,
            )
            records.append(record)
    except Exception as error:
        for path in created_files:
            path.unlink(missing_ok=True)
        if isinstance(error, EvidenceError):
            raise
        raise EvidenceError("Evidence generation failed safely.") from error

    return EvidenceReport(
        schema_version=2,
        dataset_id=insight_report.dataset_id,
        sources=tuple(source.to_dict() for source in view.sources),
        records=_assign_ranks(records),
    )


def save_evidence_report(report: EvidenceReport, *, evidence_dir: Path) -> Path:
    """Atomically persist evidence JSON outside the static directory."""

    if _DATASET_ID.fullmatch(report.dataset_id) is None:
        raise EvidenceError("Evidence dataset ID is invalid.")
    evidence_dir = _secure_directory(evidence_dir)
    final_path = evidence_dir / f"{report.dataset_id}.json"
    temporary_path = evidence_dir / (
        f".{report.dataset_id}.{secrets.token_hex(8)}.part"
    )
    payload = json.dumps(
        report.to_dict(),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    try:
        temporary_path.write_text(f"{payload}\n", encoding="utf-8")
        temporary_path.replace(final_path)
    except Exception as error:
        temporary_path.unlink(missing_ok=True)
        raise EvidenceError("Evidence report could not be saved.") from error
    return final_path


def load_evidence_payload(path: Path, *, dataset_id: str) -> dict[str, object]:
    """Load and minimally validate an evidence JSON artifact."""

    if _DATASET_ID.fullmatch(dataset_id) is None:
        raise EvidenceError("Evidence dataset ID is invalid.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceError("Saved evidence report is unreadable.") from error
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") not in {1, 2}
        or payload.get("dataset_id") != dataset_id
        or not isinstance(payload.get("records"), list)
    ):
        raise EvidenceError("Saved evidence report is invalid.")
    return payload


def chart_filename_for(
    payload: dict[str, object],
    *,
    evidence_id: str,
) -> str | None:
    """Resolve a chart filename only from a validated evidence record."""

    if _EVIDENCE_ID.fullmatch(evidence_id) is None:
        return None
    records = payload.get("records")
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, dict) or record.get("id") != evidence_id:
            continue
        chart = record.get("chart")
        if not isinstance(chart, dict):
            return None
        filename = chart.get("filename")
        if isinstance(filename, str) and _CHART_FILENAME.fullmatch(filename):
            return filename
        return None
    return None


def referenced_chart_filenames(payload: dict[str, object] | None) -> tuple[str, ...]:
    """Return only safe chart basenames referenced by an evidence report."""

    if payload is None or not isinstance(payload.get("records"), list):
        return ()
    filenames: list[str] = []
    for record in payload["records"]:
        if not isinstance(record, dict):
            continue
        chart = record.get("chart")
        filename = chart.get("filename") if isinstance(chart, dict) else None
        if isinstance(filename, str) and _CHART_FILENAME.fullmatch(filename):
            filenames.append(filename)
    return tuple(filenames)


def delete_chart_files(chart_dir: Path, filenames: tuple[str, ...]) -> None:
    """Delete only validated chart basenames from the exact configured directory."""

    secure_dir = _secure_directory(chart_dir)
    for filename in filenames:
        if _CHART_FILENAME.fullmatch(filename) is None:
            continue
        _chart_path(secure_dir, filename).unlink(missing_ok=True)


def _validate_inputs(
    view: DatasetView,
    profile: DatasetProfile,
    configuration: BusinessConfiguration,
    report: InsightReport,
) -> None:
    if len(view.sources) != 1:
        raise EvidenceError("Evidence requires one configured source view.")
    source = view.sources[0]
    if (
        report.dataset_id != configuration.dataset_id
        or report.source_sha256 != source.sha256
        or profile.source_sha256 != source.sha256
    ):
        raise EvidenceError("Evidence inputs do not refer to the same immutable dataset.")
    if tuple(item.id for item in report.insights) != tuple(
        f"INS-{index:03d}" for index in range(1, len(report.insights) + 1)
    ):
        raise EvidenceError("Insight IDs are not stable and sequential.")


def _metric_configuration(
    configuration: BusinessConfiguration,
    insight: Insight,
) -> MetricConfiguration | None:
    if insight.metric_id == "DATASET":
        return None
    return configuration.for_metric(insight.metric_id).primary_metric


def _kpi_definition(
    metric: MetricConfiguration | None,
    insight: Insight,
) -> dict[str, object]:
    if metric is None:
        return {
            "metric_id": insight.metric_id,
            "name": insight.metric,
            "metric_type": "source_column_quality",
        }
    definition = metric.to_dict()
    if metric.derived_metric is not None:
        definition["formula_label"] = metric.derived_metric.formula_label
        definition["calculation_level"] = metric.derived_metric.calculation_level
        definition["aggregation"] = metric.derived_metric.aggregation
    return definition


def _source_metadata(view: DatasetView) -> dict[str, object]:
    source = view.sources[0]
    return {
        "source_id": source.source_id,
        "filename": source.internal_filename,
        "format": source.format,
        "sha256": source.sha256,
        "worksheet": source.table_name,
    }


def _supporting_data(
    insight: Insight,
    *,
    view: DatasetView,
    profile: DatasetProfile,
    metric_configuration: MetricConfiguration | None,
) -> tuple[dict[str, object], ...]:
    observation = insight.observation
    if insight.type == "missing_data_warning":
        return tuple(
            {
                "column": column.name,
                "missing_count": column.missing_count,
                "missing_percentage": column.missing_percentage,
                "total_records": profile.row_count,
            }
            for column in profile.columns
            if column.missing_count > 0
        )
    if insight.type == "period_change":
        return (
            {
                "period": observation.get("previous_period"),
                "value": observation.get("previous_value"),
                "role": "previous",
            },
            {
                "period": observation.get("current_period"),
                "value": observation.get("current_value"),
                "role": "current",
            },
        )
    if insight.type == "trend":
        values = observation.get("period_values")
        return _dict_rows(values)
    if insight.type == "segment_ranking":
        return _dict_rows(observation.get("ranking"))
    if insight.type == "segment_contribution":
        return _dict_rows(observation.get("contributions"))
    if insight.type == "iqr_anomaly_detection" and metric_configuration is not None:
        lower = _number(observation.get("lower_bound"))
        upper = _number(observation.get("upper_bound"))
        rows: list[dict[str, object]] = []
        for row_number, value in _metric_row_values(view, metric_configuration):
            rows.append(
                {
                    "row_number": row_number,
                    "value": value,
                    "is_outlier": (
                        lower is not None
                        and upper is not None
                        and (value < lower or value > upper)
                    ),
                }
            )
        return tuple(rows)
    if insight.type == "numeric_correlation" and metric_configuration is not None:
        associated_metric = observation.get("associated_metric")
        if not isinstance(associated_metric, str):
            associated_metric = next(
                (
                    column
                    for column in insight.source_columns
                    if column not in metric_configuration.source_columns
                ),
                "",
            )
        pairs = _correlation_pairs(view, metric_configuration, associated_metric)
        if not pairs:
            return (_observation_row(observation),)
        x_values = [pair[0] for pair in pairs]
        y_values = [pair[1] for pair in pairs]
        return (
            {
                "metric": insight.metric,
                "associated_metric": associated_metric,
                "pair_count": len(pairs),
                "sum_x": math.fsum(x_values),
                "sum_y": math.fsum(y_values),
                "sum_x_squared": math.fsum(value * value for value in x_values),
                "sum_y_squared": math.fsum(value * value for value in y_values),
                "sum_xy": math.fsum(x * y for x, y in pairs),
                "coefficient": observation.get("coefficient"),
                "relationship_label": "association",
            },
        )
    return (_observation_row(observation),)


def _observation_row(observation: dict[str, object]) -> dict[str, object]:
    row: dict[str, object] = {}
    for key, value in observation.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            row[key] = value
        elif isinstance(value, list):
            row[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif isinstance(value, dict):
            row[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return row or {"status": "No tabular values were produced."}


def _dict_rows(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, dict))


def _metric_row_values(
    view: DatasetView,
    metric: MetricConfiguration,
) -> tuple[tuple[int, float], ...]:
    values: list[tuple[int, float]] = []
    for row in view.iter_rows():
        value: float | None
        if metric.metric_type == "source" and metric.source is not None:
            value = _number(row.values.get(metric.source.column))
        elif (
            metric.metric_type == "derived"
            and metric.derived_metric is not None
            and metric.derived_metric.calculation_level == "row"
        ):
            value = evaluate_derived_metric(metric.derived_metric, row.values).value
        else:
            value = None
        if value is not None and math.isfinite(value):
            values.append((row.number, float(value)))
    return tuple(values)


def _correlation_pairs(
    view: DatasetView,
    metric: MetricConfiguration,
    associated_column: str,
) -> tuple[tuple[float, float], ...]:
    metric_by_row = dict(_metric_row_values(view, metric))
    pairs: list[tuple[float, float]] = []
    for row in view.iter_rows():
        x_value = metric_by_row.get(row.number)
        y_value = _number(row.values.get(associated_column))
        if x_value is not None and y_value is not None:
            pairs.append((x_value, y_value))
    return tuple(pairs)


def _periods(insight: Insight) -> tuple[str, ...]:
    filtered = insight.filters.get("periods")
    if isinstance(filtered, list):
        return tuple(str(value) for value in filtered)
    periods: list[str] = []
    for key in ("previous_period", "current_period"):
        value = insight.observation.get(key)
        if isinstance(value, str):
            periods.append(value)
    period_values = insight.observation.get("period_values")
    if isinstance(period_values, list):
        periods.extend(
            str(item["period"])
            for item in period_values
            if isinstance(item, dict) and "period" in item
        )
    return tuple(dict.fromkeys(periods))


def _calculation_description(insight: Insight) -> str:
    observation = insight.observation
    descriptions = {
        "missing_data_warning": (
            f"Counted configured missing-value markers in {insight.metric} and divided "
            "that count by all source records."
        ),
        "period_change": (
            f"Aggregated {insight.metric} for the two latest eligible periods, subtracted "
            "the previous value from the current value, and divided by the previous value "
            "for percentage change when it was non-zero."
        ),
        "trend": (
            f"Aggregated {insight.metric} by eligible period and fitted an ordinary "
            "least-squares line to the ordered period values."
        ),
        "segment_ranking": (
            f"Grouped valid {insight.metric} values by "
            f"{observation.get('category_column', 'the configured category')}, applied "
            "the configured aggregation, then sorted values from highest to lowest."
        ),
        "segment_contribution": (
            f"Calculated each segment's change in {insight.metric} between the two "
            "comparison periods and divided it by the overall change when non-zero."
        ),
        "iqr_anomaly_detection": (
            f"Calculated Q1 and Q3 for valid row-level {insight.metric} values and flagged "
            "values outside Q1 - 1.5×IQR and Q3 + 1.5×IQR."
        ),
        "numeric_correlation": (
            f"Calculated the Pearson coefficient between valid paired values of "
            f"{insight.metric} and "
            f"{observation.get('associated_metric', 'the associated numeric column')}; "
            "this is labelled association, not causation."
        ),
        "benchmark_breach": (
            f"Compared each valid {insight.metric} value with the confirmed benchmark and "
            "divided breach count by valid record count."
        ),
        "insufficient_data_warning": (
            "Compared the available eligible record count with the deterministic minimum "
            "required for this analysis."
        ),
        "analysis_skipped": (
            "Applied the deterministic preconditions for this analysis and recorded why "
            "no numerical result was produced."
        ),
    }
    return descriptions.get(
        insight.type,
        "Recorded the exact output produced by the deterministic Python insight engine.",
    )


def _evidence_id(dataset_id: str, insight: Insight) -> str:
    identity = {
        "dataset_id": dataset_id,
        "insight_id": insight.id,
        "metric_id": insight.metric_id,
        "type": insight.type,
        "source_columns": list(insight.source_columns),
        "filters": insight.filters,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16].upper()
    return f"EVD-{digest}"


def _ranking(insight: Insight) -> EvidenceRanking:
    confidence = {"high": 1.0, "medium": 0.65, "low": 0.35}.get(
        insight.confidence, 0.35
    )
    relevance = {
        "period_change": 1.0,
        "trend": 0.95,
        "segment_contribution": 0.95,
        "segment_ranking": 0.9,
        "benchmark_breach": 0.9,
        "iqr_anomaly_detection": 0.8,
        "numeric_correlation": 0.75,
        "missing_data_warning": 0.7,
        "insufficient_data_warning": 0.4,
        "analysis_skipped": 0.25,
    }.get(insight.type, 0.5)
    impact = _impact_score(insight)
    combined = (0.5 * impact) + (0.3 * confidence) + (0.2 * relevance)
    return EvidenceRanking(
        impact=_score(impact),
        confidence=_score(confidence),
        relevance=_score(relevance),
        combined=_score(combined),
        rank=0,
    )


def _impact_score(insight: Insight) -> float:
    observation = insight.observation
    if insight.type == "missing_data_warning":
        return _bounded(_number(observation.get("missing_percentage")), scale=100)
    if insight.type == "period_change":
        percentage = _number(observation.get("percentage_change"))
        if percentage is not None:
            return _bounded(abs(percentage), scale=100)
        change = abs(_number(observation.get("absolute_change")) or 0)
        current = abs(_number(observation.get("current_value")) or 0)
        previous = abs(_number(observation.get("previous_value")) or 0)
        return min(1.0, change / max(current, previous, 1.0))
    if insight.type == "trend":
        return min(1.0, abs(_number(observation.get("r_squared")) or 0))
    if insight.type == "segment_ranking":
        ranking = observation.get("ranking")
        values = []
        if isinstance(ranking, list):
            values = [
                abs(number)
                for item in ranking
                if isinstance(item, dict)
                and (number := _number(item.get("value"))) is not None
            ]
        return (
            min(1.0, (max(values) - min(values)) / max(max(values), 1.0))
            if len(values) >= 2
            else 0.0
        )
    if insight.type == "segment_contribution":
        contributions = observation.get("contributions")
        values = []
        if isinstance(contributions, list):
            values = [
                abs(number)
                for item in contributions
                if isinstance(item, dict)
                and (number := _number(item.get("contribution_percentage"))) is not None
            ]
        return _bounded(max(values, default=0.0), scale=100)
    if insight.type == "iqr_anomaly_detection":
        count = _number(observation.get("anomaly_count")) or 0
        return min(1.0, count / max(insight.record_count, 1))
    if insight.type == "numeric_correlation":
        return min(1.0, abs(_number(observation.get("coefficient")) or 0))
    if insight.type == "benchmark_breach":
        return _bounded(_number(observation.get("breach_percentage")), scale=100)
    return 0.1 if insight.type == "insufficient_data_warning" else 0.0


def _assign_ranks(records: list[EvidenceRecord]) -> tuple[EvidenceRecord, ...]:
    ordered = sorted(
        records,
        key=lambda record: (-record.ranking.combined, record.id),
    )
    ranks = {record.id: index for index, record in enumerate(ordered, start=1)}
    return tuple(
        replace(record, ranking=replace(record.ranking, rank=ranks[record.id]))
        for record in records
    )


def _chart_type_for(
    insight: Insight,
    *,
    missing_overview_created: bool,
) -> str | None:
    if insight.type in {"period_change", "trend"}:
        return "time_trend"
    if insight.type == "segment_ranking":
        return "category_comparison"
    if insight.type == "segment_contribution":
        return "segment_contribution"
    if insight.type == "iqr_anomaly_detection":
        return "distribution_iqr_outliers"
    if insight.type == "missing_data_warning" and not missing_overview_created:
        return "missing_data_overview"
    return None


def _generate_chart(
    chart_type: str,
    *,
    insight: Insight,
    supporting_data: tuple[dict[str, object], ...],
    chart_dir: Path,
) -> ChartArtifact | None:
    if not supporting_data:
        return None
    plt = _pyplot()
    figure, axis = plt.subplots(figsize=(8, 4.8), constrained_layout=True)
    title = _safe_label(f"{_chart_title(chart_type)} — {insight.metric}")
    data_columns: tuple[str, ...]
    record_count = len(supporting_data)
    try:
        if chart_type == "time_trend":
            points = [
                (str(row.get("period", "")), _number(row.get("value")))
                for row in supporting_data
            ]
            points = [(period, value) for period, value in points if value is not None]
            if not points:
                return _close_empty(figure, plt)
            axis.plot(
                range(len(points)),
                [value for _, value in points],
                marker="o",
                color="#2166ac",
            )
            axis.set_xticks(
                range(len(points)),
                [_safe_label(period) for period, _ in points],
                rotation=30,
                ha="right",
            )
            axis.set_ylabel(_safe_label(insight.metric))
            data_columns = ("period", "value")
            record_count = len(points)
        elif chart_type == "category_comparison":
            points = [
                (str(row.get("segment", "")), _number(row.get("value")))
                for row in supporting_data[:20]
            ]
            points = [(label, value) for label, value in points if value is not None]
            if not points:
                return _close_empty(figure, plt)
            points.reverse()
            axis.barh(
                [_safe_label(label) for label, _ in points],
                [value for _, value in points],
                color="#4c78a8",
            )
            axis.set_xlabel(_safe_label(insight.metric))
            data_columns = ("segment", "value")
            record_count = len(points)
        elif chart_type == "segment_contribution":
            points = [
                (str(row.get("segment", "")), _number(row.get("absolute_change")))
                for row in supporting_data[:20]
            ]
            points = [(label, value) for label, value in points if value is not None]
            if not points:
                return _close_empty(figure, plt)
            colors = ["#2a9d8f" if value >= 0 else "#e76f51" for _, value in points]
            axis.bar(
                range(len(points)),
                [value for _, value in points],
                color=colors,
            )
            axis.axhline(0, color="#333333", linewidth=0.8)
            axis.set_xticks(
                range(len(points)),
                [_safe_label(label) for label, _ in points],
                rotation=30,
                ha="right",
            )
            axis.set_ylabel("Absolute change")
            data_columns = ("segment", "absolute_change", "contribution_percentage")
            record_count = len(points)
        elif chart_type == "distribution_iqr_outliers":
            values = [
                value
                for row in supporting_data
                if (value := _number(row.get("value"))) is not None
            ]
            if not values:
                return _close_empty(figure, plt)
            axis.boxplot(
                values,
                orientation="horizontal",
                patch_artist=True,
                boxprops={"facecolor": "#9ecae1"},
                flierprops={"marker": "o", "markerfacecolor": "#d7301f"},
            )
            axis.set_yticks([1], [_safe_label(insight.metric)])
            axis.set_xlabel("Value")
            data_columns = ("row_number", "value", "is_outlier")
            record_count = len(values)
        elif chart_type == "missing_data_overview":
            points = [
                (str(row.get("column", "")), _number(row.get("missing_percentage")))
                for row in supporting_data
            ][:20]
            points = [(label, value) for label, value in points if value is not None]
            if not points:
                return _close_empty(figure, plt)
            points.reverse()
            axis.barh(
                [_safe_label(label) for label, _ in points],
                [value for _, value in points],
                color="#f4a261",
            )
            axis.set_xlabel("Missing records (%)")
            axis.set_xlim(0, 100)
            data_columns = (
                "column",
                "missing_count",
                "missing_percentage",
                "total_records",
            )
            record_count = len(points)
        else:
            return _close_empty(figure, plt)

        axis.set_title(title)
        axis.grid(axis="y", alpha=0.2)
        filename = f"{secrets.token_hex(16)}.png"
        final_path = _chart_path(chart_dir, filename)
        temporary_path = chart_dir / f".{secrets.token_hex(16)}.part"
        try:
            figure.savefig(
                temporary_path,
                format="png",
                dpi=140,
                metadata={"Software": "AI Insight Reporter deterministic evidence layer"},
            )
            temporary_path.replace(final_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            raise
        return ChartArtifact(
            filename=filename,
            chart_type=chart_type,
            title=title,
            alt_text=_safe_label(
                f"{_chart_title(chart_type)} for {insight.metric}; "
                f"{record_count} supporting records."
            ),
            data_columns=data_columns,
            record_count=record_count,
        )
    finally:
        plt.close(figure)


def _close_empty(figure: Any, plt: Any) -> None:
    plt.close(figure)
    return None


def _pyplot() -> Any:
    """Import plotting lazily so non-chart Flask commands stay fast."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as pyplot

    return pyplot


def _chart_title(chart_type: str) -> str:
    return {
        "time_trend": "Time trend",
        "category_comparison": "Category comparison",
        "segment_contribution": "Segment contribution",
        "distribution_iqr_outliers": "Distribution and IQR outliers",
        "missing_data_overview": "Missing-data overview",
    }[chart_type]


def _safe_label(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    cleaned = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in normalized
    )
    cleaned = " ".join(cleaned.split()).replace("$", r"\$")
    if len(cleaned) > _MAX_LABEL_LENGTH:
        cleaned = f"{cleaned[: _MAX_LABEL_LENGTH - 1]}…"
    return cleaned


def _secure_directory(directory: Path) -> Path:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        resolved = directory.resolve(strict=True)
    except OSError as error:
        raise EvidenceError("Evidence output directory is unavailable.") from error
    if not resolved.is_dir():
        raise EvidenceError("Evidence output path is not a directory.")
    return resolved


def _chart_path(chart_dir: Path, filename: str) -> Path:
    if _CHART_FILENAME.fullmatch(filename) is None:
        raise EvidenceError("Generated chart filename is invalid.")
    path = (chart_dir / filename).resolve()
    if path.parent != chart_dir.resolve():
        raise EvidenceError("Generated chart path escaped the output directory.")
    return path


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().casefold() in _MISSING_MARKERS:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bounded(value: float | None, *, scale: float) -> float:
    return min(1.0, max(0.0, (value or 0.0) / scale))


def _score(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 6)
