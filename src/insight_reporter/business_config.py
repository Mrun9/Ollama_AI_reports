"""Versioned source-aware configuration and one-to-five KPI registry."""

import hashlib
import json
import math
import re
import secrets
from dataclasses import dataclass, replace
from pathlib import Path

from insight_reporter.dataset_profile import DatasetProfile
from insight_reporter.dataset_view import (
    ColumnReference,
    DatasetViewError,
    SourceManifest,
    load_column_reference,
    load_source_manifest,
    source_id_from_hash,
)
from insight_reporter.derived_metrics import (
    DerivedMetric,
    DerivedMetricError,
    convert_legacy_metric_to_formula,
    load_derived_metric,
)

_DIRECTIONS = frozenset({"higher", "lower"})
_MAX_OBJECTIVE_CHARACTERS = 2_000
_MAX_METRICS = 5
_V1_CONFIGURATION_KEYS = frozenset(
    {
        "schema_version",
        "dataset_id",
        "source_sha256",
        "primary_kpi",
        "kpi_direction",
        "date_column",
        "category_columns",
        "target_or_benchmark",
        "business_objective",
    }
)
_V2_CONFIGURATION_KEYS = _V1_CONFIGURATION_KEYS | {"metric_type", "derived_metric"}
_REGISTRY_CONFIGURATION_KEYS = frozenset(
    {
        "schema_version",
        "dataset_id",
        "sources",
        "relationships",
        "primary_metric_id",
        "metrics",
        "date_column",
        "category_columns",
        "business_objective",
    }
)


class BusinessConfigurationError(ValueError):
    """Raised when a selection does not match retained source metadata."""


@dataclass(frozen=True)
class MetricConfiguration:
    metric_id: str
    name: str
    metric_type: str
    kpi_direction: str
    target_or_benchmark: float | None
    display_format: str
    source: ColumnReference | None = None
    derived_metric: DerivedMetric | None = None

    @property
    def source_columns(self) -> tuple[str, ...]:
        if self.derived_metric is not None:
            return self.derived_metric.source_columns
        return (self.source.column,) if self.source is not None else ()

    def to_dict(self) -> dict[str, object]:
        return {
            "metric_id": self.metric_id,
            "name": self.name,
            "metric_type": self.metric_type,
            "kpi_direction": self.kpi_direction,
            "target_or_benchmark": self.target_or_benchmark,
            "display_format": self.display_format,
            "source": self.source.to_dict() if self.source is not None else None,
            "derived_metric": (
                self.derived_metric.to_dict()
                if self.derived_metric is not None
                else None
            ),
        }


@dataclass(frozen=True)
class BusinessConfiguration:
    schema_version: int
    dataset_id: str
    sources: tuple[SourceManifest, ...]
    relationships: tuple[dict[str, object], ...]
    metrics: tuple[MetricConfiguration, ...]
    primary_metric_id: str
    date_reference: ColumnReference | None
    category_references: tuple[ColumnReference, ...]
    business_objective: str

    @property
    def primary_metric(self) -> MetricConfiguration:
        for metric in self.metrics:
            if metric.metric_id == self.primary_metric_id:
                return metric
        raise BusinessConfigurationError("Primary KPI is missing from the metric registry.")

    @property
    def source_sha256(self) -> str:
        return self.sources[0].sha256

    @property
    def primary_kpi(self) -> str:
        return self.primary_metric.name

    @property
    def kpi_direction(self) -> str:
        return self.primary_metric.kpi_direction

    @property
    def target_or_benchmark(self) -> float | None:
        return self.primary_metric.target_or_benchmark

    @property
    def metric_type(self) -> str:
        return self.primary_metric.metric_type

    @property
    def derived_metric(self) -> DerivedMetric | None:
        return self.primary_metric.derived_metric

    @property
    def date_column(self) -> str | None:
        return self.date_reference.column if self.date_reference is not None else None

    @property
    def category_columns(self) -> tuple[str, ...]:
        return tuple(reference.column for reference in self.category_references)

    def for_metric(self, metric_id: str) -> "BusinessConfiguration":
        if metric_id not in {metric.metric_id for metric in self.metrics}:
            raise BusinessConfigurationError("Selected KPI is not configured.")
        return replace(self, primary_metric_id=metric_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 4,
            "dataset_id": self.dataset_id,
            "sources": [source.to_dict() for source in self.sources],
            "relationships": list(self.relationships),
            "primary_metric_id": self.primary_metric_id,
            "metrics": [metric.to_dict() for metric in self.metrics],
            "date_column": (
                self.date_reference.to_dict()
                if self.date_reference is not None
                else None
            ),
            "category_columns": [
                reference.to_dict() for reference in self.category_references
            ],
            "business_objective": self.business_objective,
        }


def validate_business_configuration(
    profile: DatasetProfile,
    *,
    dataset_id: str,
    primary_kpi: str,
    kpi_direction: str,
    date_column: str,
    category_columns: list[str],
    target_or_benchmark: str,
    business_objective: str,
    secondary_kpis: list[str] | None = None,
    existing_configuration: BusinessConfiguration | None = None,
) -> BusinessConfiguration:
    """Create or update a source-KPI registry from actual profile candidates."""

    selected = [primary_kpi, *(secondary_kpis or [])]
    if not selected or len(selected) != len(set(selected)):
        raise BusinessConfigurationError("KPI selections must not contain duplicates.")
    if len(selected) > _MAX_METRICS:
        raise BusinessConfigurationError(f"Select at most {_MAX_METRICS} KPIs.")
    if any(column not in profile.kpi_candidates for column in selected):
        raise BusinessConfigurationError(
            "Select measurable KPIs from the available candidates."
        )
    source = _single_source(profile, dataset_id)
    source_metrics = tuple(
        _source_metric(
            source,
            column,
            direction=kpi_direction if column == primary_kpi else "higher",
            target=(
                _parse_optional_target(target_or_benchmark)
                if column == primary_kpi
                else None
            ),
        )
        for column in selected
    )
    retained_derived = (
        tuple(
            metric
            for metric in existing_configuration.metrics
            if metric.metric_type == "derived"
        )
        if existing_configuration is not None
        else ()
    )
    metrics = _deduplicate_metrics((*source_metrics, *retained_derived))
    if len(metrics) > _MAX_METRICS:
        raise BusinessConfigurationError(
            f"The KPI registry supports at most {_MAX_METRICS} metrics."
        )
    return _build_configuration(
        profile,
        dataset_id=dataset_id,
        metrics=metrics,
        primary_metric_id=source_metrics[0].metric_id,
        date_column=date_column,
        category_columns=category_columns,
        business_objective=business_objective,
    )


def validate_derived_business_configuration(
    profile: DatasetProfile,
    *,
    dataset_id: str,
    derived_metric: DerivedMetric,
    kpi_direction: str,
    date_column: str,
    category_columns: list[str],
    target_or_benchmark: str,
    business_objective: str,
    existing_configuration: BusinessConfiguration | None = None,
    metric_role: str = "primary",
) -> BusinessConfiguration:
    """Add a revalidated formula to a source-aware KPI registry."""

    source = _single_source(profile, dataset_id)
    try:
        safe_metric = load_derived_metric(
            profile,
            derived_metric.to_dict(),
            source_id=source.source_id,
        )
        safe_metric = convert_legacy_metric_to_formula(
            profile,
            safe_metric,
            source_id=source.source_id,
        )
    except DerivedMetricError as error:
        raise BusinessConfigurationError(str(error)) from error
    if kpi_direction not in _DIRECTIONS:
        raise BusinessConfigurationError("KPI direction must be either higher or lower.")
    if metric_role not in {"primary", "secondary"}:
        raise BusinessConfigurationError("Derived KPI role must be primary or secondary.")
    metric = _derived_registry_metric(
        source,
        safe_metric,
        direction=kpi_direction,
        target=_parse_optional_target(target_or_benchmark),
    )
    existing_metrics = existing_configuration.metrics if existing_configuration else ()
    without_same_name = tuple(
        item
        for item in existing_metrics
        if item.name.casefold() != metric.name.casefold()
    )
    metrics = _deduplicate_metrics((*without_same_name, metric))
    if len(metrics) > _MAX_METRICS:
        raise BusinessConfigurationError(
            f"The KPI registry supports at most {_MAX_METRICS} metrics."
        )
    replacing_primary = (
        existing_configuration is not None
        and existing_configuration.primary_metric.name.casefold()
        == metric.name.casefold()
    )
    primary_metric_id = (
        metric.metric_id
        if metric_role == "primary"
        or existing_configuration is None
        or replacing_primary
        else existing_configuration.primary_metric_id
    )
    return _build_configuration(
        profile,
        dataset_id=dataset_id,
        metrics=metrics,
        primary_metric_id=primary_metric_id,
        date_column=date_column,
        category_columns=category_columns,
        business_objective=business_objective,
    )


def set_primary_metric(
    configuration: BusinessConfiguration, metric_id: str
) -> BusinessConfiguration:
    """Return a registry with a different existing primary KPI."""

    if metric_id not in {metric.metric_id for metric in configuration.metrics}:
        raise BusinessConfigurationError("Selected primary KPI is not configured.")
    return replace(configuration, primary_metric_id=metric_id)


def remove_metric(
    configuration: BusinessConfiguration, metric_id: str
) -> BusinessConfiguration:
    """Remove a secondary KPI while preserving at least one configured metric."""

    if metric_id == configuration.primary_metric_id:
        raise BusinessConfigurationError("Choose a different primary KPI before removing this KPI.")
    metrics = tuple(
        metric for metric in configuration.metrics if metric.metric_id != metric_id
    )
    if len(metrics) == len(configuration.metrics):
        raise BusinessConfigurationError("Selected KPI is not configured.")
    return replace(configuration, metrics=metrics)


def update_metric_settings(
    configuration: BusinessConfiguration,
    metric_id: str,
    *,
    kpi_direction: str,
    target_or_benchmark: str,
) -> BusinessConfiguration:
    """Update direction and optional benchmark for one configured KPI."""

    if kpi_direction not in _DIRECTIONS:
        raise BusinessConfigurationError("KPI direction must be either higher or lower.")
    target = _parse_optional_target(target_or_benchmark)
    found = False
    metrics: list[MetricConfiguration] = []
    for metric in configuration.metrics:
        if metric.metric_id == metric_id:
            found = True
            metrics.append(
                replace(
                    metric,
                    kpi_direction=kpi_direction,
                    target_or_benchmark=target,
                )
            )
        else:
            metrics.append(metric)
    if not found:
        raise BusinessConfigurationError("Selected KPI is not configured.")
    return replace(configuration, metrics=tuple(metrics))


def save_business_configuration(
    configuration: BusinessConfiguration, *, configuration_dir: Path
) -> Path:
    """Atomically persist the validated registry outside the static tree."""

    configuration_dir.mkdir(parents=True, exist_ok=True)
    final_path = configuration_dir / f"{configuration.dataset_id}.json"
    temporary_path = configuration_dir / (
        f".{configuration.dataset_id}.{secrets.token_hex(8)}.part"
    )
    payload = json.dumps(
        configuration.to_dict(),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    try:
        temporary_path.write_text(f"{payload}\n", encoding="utf-8")
        temporary_path.replace(final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return final_path


def load_business_configuration(
    path: Path, *, profile: DatasetProfile
) -> BusinessConfiguration:
    """Load v1-v4 configurations and migrate legacy forms in memory."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BusinessConfigurationError("Saved business configuration is unreadable.") from error
    if not isinstance(payload, dict):
        raise BusinessConfigurationError("Saved business configuration has an invalid shape.")
    version = payload.get("schema_version")
    if version in {1, 2}:
        return _load_legacy_configuration(payload, profile=profile)
    if version not in {3, 4} or set(payload) != _REGISTRY_CONFIGURATION_KEYS:
        raise BusinessConfigurationError("Saved business configuration version is unsupported.")
    return _load_registry_configuration(payload, profile=profile)


def _build_configuration(
    profile: DatasetProfile,
    *,
    dataset_id: str,
    metrics: tuple[MetricConfiguration, ...],
    primary_metric_id: str,
    date_column: str,
    category_columns: list[str],
    business_objective: str,
) -> BusinessConfiguration:
    if re.fullmatch(r"[0-9a-f]{32}", dataset_id) is None:
        raise BusinessConfigurationError("Dataset ID is invalid.")
    if not 1 <= len(metrics) <= _MAX_METRICS:
        raise BusinessConfigurationError(
            f"Configure between one and {_MAX_METRICS} KPIs."
        )
    if len({metric.metric_id for metric in metrics}) != len(metrics):
        raise BusinessConfigurationError("Configured metric IDs must be unique.")
    if len({metric.name.casefold() for metric in metrics}) != len(metrics):
        raise BusinessConfigurationError("Configured KPI names must be unique.")
    if primary_metric_id not in {metric.metric_id for metric in metrics}:
        raise BusinessConfigurationError("Primary KPI must be present in the registry.")
    source = _single_source(profile, dataset_id)
    metrics = tuple(
        _revalidate_registry_metric(profile, source, metric) for metric in metrics
    )
    selected_date = date_column.strip() or None
    if selected_date is not None and selected_date not in profile.date_candidates:
        raise BusinessConfigurationError("Selected date column is not a valid date candidate.")
    if len(category_columns) != len(set(category_columns)):
        raise BusinessConfigurationError("Category selections must not contain duplicates.")
    if any(column not in profile.category_candidates for column in category_columns):
        raise BusinessConfigurationError(
            "Category selections must come from the available category candidates."
        )
    objective = business_objective.strip()
    if not objective:
        raise BusinessConfigurationError("Business objective is required.")
    if len(objective) > _MAX_OBJECTIVE_CHARACTERS:
        raise BusinessConfigurationError(
            f"Business objective must be at most {_MAX_OBJECTIVE_CHARACTERS} characters."
        )
    return BusinessConfiguration(
        schema_version=4,
        dataset_id=dataset_id,
        sources=(source,),
        relationships=(),
        metrics=metrics,
        primary_metric_id=primary_metric_id,
        date_reference=(
            ColumnReference(source.source_id, selected_date)
            if selected_date is not None
            else None
        ),
        category_references=tuple(
            ColumnReference(source.source_id, column) for column in category_columns
        ),
        business_objective=objective,
    )


def _single_source(profile: DatasetProfile, dataset_id: str) -> SourceManifest:
    if re.fullmatch(r"[0-9a-f]{32}", dataset_id) is None:
        raise BusinessConfigurationError("Dataset ID is invalid.")
    return SourceManifest(
        source_id=source_id_from_hash(
            profile.source_sha256,
            profile.source_table_name,
        ),
        format=profile.source_format,
        internal_filename=f"{dataset_id}.{profile.source_format}",
        sha256=profile.source_sha256,
        row_count=profile.row_count,
        column_count=profile.column_count,
        table_name=profile.source_table_name,
    )


def _source_metric(
    source: SourceManifest,
    column: str,
    *,
    direction: str,
    target: float | None,
) -> MetricConfiguration:
    if direction not in _DIRECTIONS:
        raise BusinessConfigurationError("KPI direction must be either higher or lower.")
    reference = ColumnReference(source.source_id, column)
    return MetricConfiguration(
        metric_id=_metric_id(
            {"metric_type": "source", "source": reference.to_dict()}
        ),
        name=column,
        metric_type="source",
        kpi_direction=direction,
        target_or_benchmark=target,
        display_format="number",
        source=reference,
    )


def _derived_registry_metric(
    source: SourceManifest,
    metric: DerivedMetric,
    *,
    direction: str,
    target: float | None,
) -> MetricConfiguration:
    return MetricConfiguration(
        metric_id=_metric_id(
            {
                "metric_type": "derived",
                "source_id": source.source_id,
                "definition": metric.to_dict(),
            }
        ),
        name=metric.name,
        metric_type="derived",
        kpi_direction=direction,
        target_or_benchmark=target,
        display_format=metric.display_format,
        derived_metric=metric,
    )


def _revalidate_registry_metric(
    profile: DatasetProfile,
    source: SourceManifest,
    metric: MetricConfiguration,
) -> MetricConfiguration:
    if metric.kpi_direction not in _DIRECTIONS:
        raise BusinessConfigurationError("Configured KPI direction is invalid.")
    target = metric.target_or_benchmark
    if target is not None and not math.isfinite(target):
        raise BusinessConfigurationError("Configured KPI target is invalid.")
    if metric.metric_type == "source" and metric.source is not None:
        if metric.source.source_id != source.source_id:
            raise BusinessConfigurationError("Configured KPI uses a different source.")
        if metric.source.column not in profile.kpi_candidates:
            raise BusinessConfigurationError("Configured source KPI is not measurable.")
        safe = _source_metric(
            source,
            metric.source.column,
            direction=metric.kpi_direction,
            target=target,
        )
    elif metric.metric_type == "derived" and metric.derived_metric is not None:
        try:
            derived = load_derived_metric(
                profile,
                metric.derived_metric.to_dict(),
                source_id=source.source_id,
            )
        except DerivedMetricError as error:
            raise BusinessConfigurationError(str(error)) from error
        safe = _derived_registry_metric(
            source,
            derived,
            direction=metric.kpi_direction,
            target=target,
        )
    else:
        raise BusinessConfigurationError("Configured KPI definition is invalid.")
    if safe != metric:
        raise BusinessConfigurationError("Configured KPI identity is invalid.")
    return safe


def _metric_id(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"MET-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:12].upper()}"


def _deduplicate_metrics(
    metrics: tuple[MetricConfiguration, ...],
) -> tuple[MetricConfiguration, ...]:
    result: list[MetricConfiguration] = []
    for metric in metrics:
        if metric.metric_id not in {item.metric_id for item in result}:
            result.append(metric)
    return tuple(result)


def _load_registry_configuration(
    payload: dict[str, object], *, profile: DatasetProfile
) -> BusinessConfiguration:
    dataset_id = payload.get("dataset_id")
    sources_payload = payload.get("sources")
    relationships = payload.get("relationships")
    metrics_payload = payload.get("metrics")
    primary_metric_id = payload.get("primary_metric_id")
    categories_payload = payload.get("category_columns")
    objective = payload.get("business_objective")
    if not isinstance(dataset_id, str) or not isinstance(primary_metric_id, str):
        raise BusinessConfigurationError("Saved configuration contains invalid IDs.")
    if not isinstance(sources_payload, list) or len(sources_payload) != 1:
        raise BusinessConfigurationError("Saved configuration must contain one current source.")
    if relationships != []:
        raise BusinessConfigurationError("Source relationships are not supported yet.")
    try:
        sources = tuple(load_source_manifest(item) for item in sources_payload)
    except DatasetViewError as error:
        raise BusinessConfigurationError(str(error)) from error
    expected_source = _single_source(profile, dataset_id)
    if sources != (expected_source,):
        raise BusinessConfigurationError(
            "Saved configuration does not match the retained dataset."
        )
    if not isinstance(metrics_payload, list):
        raise BusinessConfigurationError("Saved metric registry is invalid.")
    metrics = tuple(
        _load_metric(item, profile=profile, source=sources[0])
        for item in metrics_payload
    )
    try:
        date_reference = (
            load_column_reference(payload.get("date_column"), sources=sources)
            if payload.get("date_column") is not None
            else None
        )
        if not isinstance(categories_payload, list):
            raise DatasetViewError("Saved categories are invalid.")
        category_references = tuple(
            load_column_reference(item, sources=sources) for item in categories_payload
        )
    except DatasetViewError as error:
        raise BusinessConfigurationError(str(error)) from error
    if not isinstance(objective, str):
        raise BusinessConfigurationError("Saved business objective is invalid.")
    return _build_configuration(
        profile,
        dataset_id=dataset_id,
        metrics=metrics,
        primary_metric_id=primary_metric_id,
        date_column=date_reference.column if date_reference else "",
        category_columns=[reference.column for reference in category_references],
        business_objective=objective,
    )


def _load_metric(
    payload: object, *, profile: DatasetProfile, source: SourceManifest
) -> MetricConfiguration:
    expected = {
        "metric_id",
        "name",
        "metric_type",
        "kpi_direction",
        "target_or_benchmark",
        "display_format",
        "source",
        "derived_metric",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise BusinessConfigurationError("Saved metric has an invalid shape.")
    metric_id = payload.get("metric_id")
    name = payload.get("name")
    metric_type = payload.get("metric_type")
    direction = payload.get("kpi_direction")
    display_format = payload.get("display_format")
    target = payload.get("target_or_benchmark")
    if not all(
        isinstance(value, str)
        for value in (metric_id, name, metric_type, direction, display_format)
    ):
        raise BusinessConfigurationError("Saved metric contains invalid text.")
    if direction not in _DIRECTIONS:
        raise BusinessConfigurationError("Saved metric direction is invalid.")
    if isinstance(target, bool) or (
        target is not None and not isinstance(target, int | float)
    ):
        raise BusinessConfigurationError("Saved metric target is invalid.")
    safe_target = None if target is None else float(target)
    if safe_target is not None and not math.isfinite(safe_target):
        raise BusinessConfigurationError("Saved metric target is invalid.")
    if metric_type == "source":
        try:
            reference = load_column_reference(payload.get("source"), sources=(source,))
        except DatasetViewError as error:
            raise BusinessConfigurationError(str(error)) from error
        if reference.column not in profile.kpi_candidates:
            raise BusinessConfigurationError("Saved source KPI is not measurable.")
        metric = _source_metric(
            source, reference.column, direction=direction, target=safe_target
        )
    elif metric_type == "derived" and payload.get("source") is None:
        try:
            derived = load_derived_metric(
                profile, payload.get("derived_metric"), source_id=source.source_id
            )
        except DerivedMetricError as error:
            raise BusinessConfigurationError(str(error)) from error
        metric = _derived_registry_metric(
            source, derived, direction=direction, target=safe_target
        )
    else:
        raise BusinessConfigurationError("Saved metric type is invalid.")
    if (
        metric.metric_id != metric_id
        or metric.name != name
        or metric.display_format != display_format
    ):
        raise BusinessConfigurationError("Saved metric identity is invalid.")
    return metric


def _load_legacy_configuration(
    payload: dict[str, object], *, profile: DatasetProfile
) -> BusinessConfiguration:
    version = payload.get("schema_version")
    expected = _V1_CONFIGURATION_KEYS if version == 1 else _V2_CONFIGURATION_KEYS
    if set(payload) != expected:
        raise BusinessConfigurationError("Saved legacy configuration has an invalid shape.")
    dataset_id = payload.get("dataset_id")
    primary_kpi = payload.get("primary_kpi")
    direction = payload.get("kpi_direction")
    date = payload.get("date_column")
    categories = payload.get("category_columns")
    target = payload.get("target_or_benchmark")
    objective = payload.get("business_objective")
    if not all(
        isinstance(value, str)
        for value in (dataset_id, primary_kpi, direction, objective)
    ):
        raise BusinessConfigurationError("Saved legacy configuration contains invalid text.")
    if payload.get("source_sha256") != profile.source_sha256:
        raise BusinessConfigurationError(
            "Saved configuration does not match the retained dataset."
        )
    if date is not None and not isinstance(date, str):
        raise BusinessConfigurationError("Saved legacy date is invalid.")
    if not isinstance(categories, list) or not all(
        isinstance(column, str) for column in categories
    ):
        raise BusinessConfigurationError("Saved legacy categories are invalid.")
    if isinstance(target, bool) or (
        target is not None and not isinstance(target, int | float)
    ):
        raise BusinessConfigurationError("Saved legacy target is invalid.")
    common = {
        "profile": profile,
        "dataset_id": dataset_id,
        "kpi_direction": direction,
        "date_column": date or "",
        "category_columns": categories,
        "target_or_benchmark": "" if target is None else str(target),
        "business_objective": objective,
    }
    if version == 1 or (
        payload.get("metric_type") == "source"
        and payload.get("derived_metric") is None
    ):
        return validate_business_configuration(**common, primary_kpi=primary_kpi)
    if payload.get("metric_type") != "derived":
        raise BusinessConfigurationError("Saved legacy metric type is invalid.")
    try:
        derived = load_derived_metric(profile, payload.get("derived_metric"))
    except DerivedMetricError as error:
        raise BusinessConfigurationError(str(error)) from error
    if primary_kpi != derived.name:
        raise BusinessConfigurationError("Saved derived KPI name does not match.")
    return validate_derived_business_configuration(
        **common,
        derived_metric=derived,
    )


def _parse_optional_target(value: str) -> float | None:
    candidate = value.strip()
    if not candidate:
        return None
    try:
        target = float(candidate)
    except ValueError as error:
        raise BusinessConfigurationError("Target or benchmark must be a number.") from error
    if not math.isfinite(target):
        raise BusinessConfigurationError("Target or benchmark must be a finite number.")
    return target
