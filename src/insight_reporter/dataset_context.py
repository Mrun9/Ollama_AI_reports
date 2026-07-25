"""Reusable, bounded dataset context for human and local-model assistance."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from insight_reporter.business_config import BusinessConfiguration
from insight_reporter.dataset_profile import ColumnType, DatasetProfile
from insight_reporter.dataset_view import DatasetView

MAX_UI_CATEGORY_VALUES = 20
MAX_MODEL_CATEGORY_VALUES = 10
MAX_MODEL_NUMERIC_COLUMNS = 40
MAX_MODEL_CATEGORY_COLUMNS = 20
MAX_MODEL_DATE_COLUMNS = 10
MAX_MODEL_CONTEXT_CHARACTERS = 8_000


@dataclass(frozen=True)
class ContextItem:
    token: str
    name: str
    kind: str
    details: dict[str, object]
    insert_text: str

    def to_dict(self) -> dict[str, object]:
        return {
            "token": self.token,
            "name": self.name,
            "kind": self.kind,
            "details": self.details,
            "insert_text": self.insert_text,
        }


@dataclass(frozen=True)
class DatasetContext:
    dataset_id: str
    source: dict[str, object]
    row_count: int
    metrics: tuple[ContextItem, ...]
    numeric_columns: tuple[ContextItem, ...]
    categories: tuple[ContextItem, ...]
    dates: tuple[ContextItem, ...]
    booleans: tuple[ContextItem, ...]
    other_columns: tuple[ContextItem, ...]
    saved_visualizations: tuple[dict[str, object], ...]
    evidence: tuple[dict[str, object], ...]

    def item(self, token: str) -> ContextItem | None:
        for collection in (
            self.metrics,
            self.numeric_columns,
            self.categories,
            self.dates,
            self.booleans,
            self.other_columns,
        ):
            for item in collection:
                if item.token == token:
                    return item
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "source": self.source,
            "row_count": self.row_count,
            "metrics": [item.to_dict() for item in self.metrics],
            "numeric_columns": [
                item.to_dict() for item in self.numeric_columns
            ],
            "categories": [item.to_dict() for item in self.categories],
            "dates": [item.to_dict() for item in self.dates],
            "booleans": [item.to_dict() for item in self.booleans],
            "other_columns": [
                item.to_dict() for item in self.other_columns
            ],
            "saved_visualizations": list(self.saved_visualizations),
            "evidence": list(self.evidence),
        }


def build_dataset_context(
    view: DatasetView,
    *,
    profile: DatasetProfile,
    configuration: BusinessConfiguration | None,
    saved_visualizations: tuple[object, ...] = (),
    evidence_payload: dict[str, object] | None = None,
) -> DatasetContext:
    """Build deterministic UI context without exposing raw free-text rows."""

    source = view.sources[0]
    category_values = _category_values(view, profile)
    metrics: list[ContextItem] = []
    if configuration is not None:
        for index, metric in enumerate(configuration.metrics, start=1):
            formula = (
                metric.derived_metric.formula_label
                if metric.derived_metric is not None
                else None
            )
            aggregation = (
                metric.derived_metric.aggregation
                if metric.derived_metric is not None
                else "sum"
            )
            metrics.append(
                ContextItem(
                    token=f"M{index}",
                    name=metric.name,
                    kind="configured_kpi",
                    details={
                        "metric_id": metric.metric_id,
                        "metric_type": metric.metric_type,
                        "primary": (
                            metric.metric_id == configuration.primary_metric_id
                        ),
                        "formula": formula,
                        "calculation_level": (
                            metric.derived_metric.calculation_level
                            if metric.derived_metric is not None
                            else "row"
                        ),
                        "aggregation": aggregation,
                        "display_format": metric.display_format,
                        "direction": metric.kpi_direction,
                    },
                    insert_text=f"@M{index}",
                )
            )

    numeric: list[ContextItem] = []
    categories: list[ContextItem] = []
    dates: list[ContextItem] = []
    booleans: list[ContextItem] = []
    other: list[ContextItem] = []
    numeric_index = category_index = date_index = boolean_index = other_index = 0
    for column in profile.columns:
        if column.inferred_type is ColumnType.NUMERIC:
            numeric_index += 1
            statistics = (
                asdict(column.numeric_statistics)
                if column.numeric_statistics is not None
                else None
            )
            numeric.append(
                ContextItem(
                    token=f"N{numeric_index}",
                    name=column.name,
                    kind="numeric",
                    details={
                        "statistics": statistics,
                        "missing_count": column.missing_count,
                        "unique_count": column.unique_count,
                        "available_for_visualization": (
                            not column.is_constant and not column.is_empty
                        ),
                    },
                    insert_text=f"@N{numeric_index}",
                )
            )
        elif column.inferred_type is ColumnType.CATEGORICAL:
            category_index += 1
            values, truncated = category_values.get(column.name, ((), False))
            categories.append(
                ContextItem(
                    token=f"C{category_index}",
                    name=column.name,
                    kind="categorical",
                    details={
                        "unique_count": column.unique_count,
                        "missing_count": column.missing_count,
                        "values": list(values),
                        "values_truncated": truncated,
                        "available_for_visualization": (
                            column.name in profile.category_candidates
                        ),
                    },
                    insert_text=f"@C{category_index}",
                )
            )
        elif column.inferred_type is ColumnType.DATETIME:
            date_index += 1
            dates.append(
                ContextItem(
                    token=f"D{date_index}",
                    name=column.name,
                    kind="date",
                    details={
                        "date_range": (
                            asdict(column.date_range)
                            if column.date_range is not None
                            else None
                        ),
                        "missing_count": column.missing_count,
                        "available_for_visualization": (
                            column.name in profile.date_candidates
                        ),
                    },
                    insert_text=f"@D{date_index}",
                )
            )
        elif column.inferred_type is ColumnType.BOOLEAN:
            boolean_index += 1
            values, truncated = category_values.get(column.name, ((), False))
            booleans.append(
                ContextItem(
                    token=f"B{boolean_index}",
                    name=column.name,
                    kind="boolean",
                    details={
                        "unique_count": column.unique_count,
                        "missing_count": column.missing_count,
                        "values": list(values),
                        "values_truncated": truncated,
                        "available_for_visualization": (
                            column.name in profile.category_candidates
                        ),
                    },
                    insert_text=f"@B{boolean_index}",
                )
            )
        else:
            other_index += 1
            other.append(
                ContextItem(
                    token=f"O{other_index}",
                    name=column.name,
                    kind=column.inferred_type.value,
                    details={
                        "unique_count": column.unique_count,
                        "missing_count": column.missing_count,
                        "available_for_visualization": False,
                    },
                    insert_text=f"@O{other_index}",
                )
            )

    return DatasetContext(
        dataset_id=(
            configuration.dataset_id
            if configuration is not None
            else source.internal_filename.split(".", 1)[0]
        ),
        source={
            "filename": source.internal_filename,
            "format": source.format,
            "sha256": source.sha256,
            "worksheet": source.table_name,
        },
        row_count=profile.row_count,
        metrics=tuple(metrics),
        numeric_columns=tuple(numeric),
        categories=tuple(categories),
        dates=tuple(dates),
        booleans=tuple(booleans),
        other_columns=tuple(other),
        saved_visualizations=_saved_visualization_context(saved_visualizations),
        evidence=_evidence_context(evidence_payload),
    )


def build_model_context(
    context: DatasetContext,
    *,
    user_request: str,
) -> dict[str, object]:
    """Return compact tokenized context under a strict character budget."""

    mentioned_tokens = {
        item.token
        for item in (
            *context.metrics,
            *context.numeric_columns,
            *context.categories,
            *context.dates,
            *context.booleans,
        )
        if f"@{item.token}".casefold() in user_request.casefold()
        or item.name.casefold() in user_request.casefold()
    }
    numeric = _prioritize(
        context.numeric_columns,
        mentioned_tokens,
        MAX_MODEL_NUMERIC_COLUMNS,
    )
    categories = _prioritize(
        (*context.categories, *context.booleans),
        mentioned_tokens,
        MAX_MODEL_CATEGORY_COLUMNS,
    )
    dates = _prioritize(
        context.dates,
        mentioned_tokens,
        MAX_MODEL_DATE_COLUMNS,
    )
    payload: dict[str, object] = {
        "row_count": context.row_count,
        "metrics": [_model_metric(item) for item in context.metrics],
        "numeric_columns": [_model_numeric(item) for item in numeric],
        "categories": [_model_category(item) for item in categories],
        "dates": [_model_date(item) for item in dates],
        "record_count_token": "COUNT_RECORDS",
        "allowed_chart_types": [
            "time_line",
            "category_bar",
            "category_bar_horizontal",
            "scatter",
            "histogram",
            "box",
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > MAX_MODEL_CONTEXT_CHARACTERS:
        # Preserve all configured KPIs and explicitly mentioned fields. Remove
        # non-mentioned fields from the end until the budget is met.
        compact_numeric = list(numeric)
        compact_categories = list(categories)
        for compact, key, converter in (
            (compact_numeric, "numeric_columns", _model_numeric),
            (compact_categories, "categories", _model_category),
        ):
            while len(encoded) > MAX_MODEL_CONTEXT_CHARACTERS and compact:
                removable = next(
                    (
                        index
                        for index in range(len(compact) - 1, -1, -1)
                        if compact[index].token not in mentioned_tokens
                    ),
                    None,
                )
                if removable is None:
                    break
                compact.pop(removable)
                payload[key] = [converter(item) for item in compact]
                encoded = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
        if len(encoded) > MAX_MODEL_CONTEXT_CHARACTERS:
            for item in payload["categories"]:
                if isinstance(item, dict):
                    item["values"] = []
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
    if len(encoded) > MAX_MODEL_CONTEXT_CHARACTERS:
        raise ValueError(
            "Dataset context is too large for local visualization assistance."
        )
    return payload


def context_token_maps(
    context: DatasetContext,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return trusted model-token mappings for measures, axes, and categories."""

    measures = {
        item.token: (
            f"metric:{item.details['metric_id']}"
            if item.kind == "configured_kpi"
            else f"column:{item.name}"
        )
        for item in (*context.metrics, *context.numeric_columns)
        if item.kind == "configured_kpi"
        or bool(item.details.get("available_for_visualization"))
    }
    measures["COUNT_RECORDS"] = "count:records"
    axes = {
        item.token: item.name
        for item in (
            *context.numeric_columns,
            *context.categories,
            *context.dates,
            *context.booleans,
        )
        if bool(item.details.get("available_for_visualization"))
    }
    category_tokens = {
        item.token: item.name
        for item in (*context.categories, *context.booleans)
        if bool(item.details.get("available_for_visualization"))
    }
    return measures, axes, category_tokens


def formula_insert_text(item: ContextItem) -> str:
    return f"[{item.name.replace(']', ']]')}]"


def _category_values(
    view: DatasetView,
    profile: DatasetProfile,
) -> dict[str, tuple[tuple[str, ...], bool]]:
    eligible = {
        column.name
        for column in profile.columns
        if column.inferred_type in {ColumnType.CATEGORICAL, ColumnType.BOOLEAN}
    }
    values: dict[str, set[str]] = {column: set() for column in eligible}
    for row in view.iter_rows():
        for column in eligible:
            value = row.values[column].strip()
            if value and value.casefold() not in {
                "na",
                "n/a",
                "null",
                "none",
                "nan",
            }:
                values[column].add(value)
    return {
        column: (
            tuple(sorted(items, key=str.casefold)[:MAX_UI_CATEGORY_VALUES]),
            len(items) > MAX_UI_CATEGORY_VALUES,
        )
        for column, items in values.items()
    }


def _prioritize(
    items: tuple[ContextItem, ...],
    mentioned_tokens: set[str],
    maximum: int,
) -> tuple[ContextItem, ...]:
    return tuple(
        sorted(
            items,
            key=lambda item: (
                item.token not in mentioned_tokens,
                int(item.token[1:]),
            ),
        )[:maximum]
    )


def _model_metric(item: ContextItem) -> dict[str, object]:
    return {
        "token": item.token,
        "name": item.name,
        "metric_type": item.details["metric_type"],
        "primary": item.details["primary"],
        "formula": item.details["formula"],
        "calculation_level": item.details["calculation_level"],
        "aggregation": item.details["aggregation"],
        "display_format": item.details["display_format"],
    }


def _model_numeric(item: ContextItem) -> dict[str, object]:
    statistics = item.details.get("statistics")
    return {
        "token": item.token,
        "name": item.name,
        "minimum": (
            statistics.get("minimum") if isinstance(statistics, dict) else None
        ),
        "maximum": (
            statistics.get("maximum") if isinstance(statistics, dict) else None
        ),
        "mean": statistics.get("mean") if isinstance(statistics, dict) else None,
        "missing_count": item.details["missing_count"],
        "available": item.details["available_for_visualization"],
    }


def _model_category(item: ContextItem) -> dict[str, object]:
    values = item.details.get("values")
    return {
        "token": item.token,
        "name": item.name,
        "values": (
            list(values[:MAX_MODEL_CATEGORY_VALUES])
            if isinstance(values, list)
            else []
        ),
        "unique_count": item.details["unique_count"],
        "available": item.details["available_for_visualization"],
    }


def _model_date(item: ContextItem) -> dict[str, object]:
    return {
        "token": item.token,
        "name": item.name,
        "date_range": item.details["date_range"],
        "available": item.details["available_for_visualization"],
    }


def _saved_visualization_context(
    artifacts: tuple[object, ...],
) -> tuple[dict[str, object], ...]:
    output: list[dict[str, object]] = []
    for index, artifact in enumerate(artifacts, start=1):
        spec = getattr(artifact, "spec", None)
        output.append(
            {
                "token": f"V{index}",
                "visualization_id": getattr(artifact, "visualization_id", None),
                "title": getattr(spec, "title", ""),
                "classification": getattr(artifact, "classification", ""),
                "include_in_report": getattr(spec, "include_in_report", False),
            }
        )
    return tuple(output)


def _evidence_context(
    payload: dict[str, object] | None,
) -> tuple[dict[str, object], ...]:
    if payload is None or not isinstance(payload.get("records"), list):
        return ()
    output: list[dict[str, object]] = []
    for record in payload["records"]:
        if not isinstance(record, dict):
            continue
        output.append(
            {
                "evidence_id": record.get("id"),
                "insight_type": record.get("insight_type"),
                "metric": record.get("metric"),
                "has_chart": isinstance(record.get("chart"), dict),
            }
        )
    return tuple(output)
