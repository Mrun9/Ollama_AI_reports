"""Validated, source-bound report selections for Milestone 5A."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from insight_reporter.business_config import BusinessConfiguration
from insight_reporter.manual_visualization_store import ManualVisualizationArtifact
from insight_reporter.visualization_builder import VisualizationArtifact

AUDIENCES = ("executive", "management", "analyst", "general")
TONES = ("professional", "concise", "technical")
DETAIL_LEVELS = ("brief", "standard", "detailed")

_DATASET_ID = re.compile(r"[0-9a-f]{32}")
_EVIDENCE_ID = re.compile(r"EVD-[0-9A-F]{16}")
_DATASET_EVIDENCE_METRIC_ID = "DATASET"
_MAX_TITLE_CHARACTERS = 200
_MAX_BRANDING_CHARACTERS = 200
_MAX_OBJECTIVE_CHARACTERS = 2_000
_MAX_NOTES_CHARACTERS = 5_000
_MAX_EVIDENCE_SELECTIONS = 500
_MAX_VISUALIZATION_SELECTIONS = 100
_REPORT_KEYS_V1 = frozenset(
    {
        "schema_version",
        "dataset_id",
        "sources",
        "business_configuration_sha256",
        "evidence_sha256",
        "visualization_sha256s",
        "title",
        "business_objective",
        "audience",
        "tone",
        "detail_level",
        "user_notes",
        "include_evidence_appendix",
        "selected_metric_ids",
        "selected_evidence_ids",
        "selected_visualization_ids",
    }
)
_REPORT_KEYS_V2 = _REPORT_KEYS_V1 | {
    "company_name",
    "report_author",
}
_REPORT_KEYS_V3 = _REPORT_KEYS_V2 | {
    "manual_board_sha256s",
    "selected_manual_board_ids",
}


class ReportConfigurationError(ValueError):
    """Raised when a report selection is invalid, stale, or unreadable."""


@dataclass(frozen=True)
class ReportConfiguration:
    schema_version: int
    dataset_id: str
    sources: tuple[dict[str, object], ...]
    business_configuration_sha256: str
    evidence_sha256: str | None
    visualization_sha256s: dict[str, str]
    title: str
    company_name: str
    report_author: str
    business_objective: str
    audience: str
    tone: str
    detail_level: str
    user_notes: str
    include_evidence_appendix: bool
    selected_metric_ids: tuple[str, ...]
    selected_evidence_ids: tuple[str, ...]
    selected_visualization_ids: tuple[str, ...]
    manual_board_sha256s: dict[str, str] = field(default_factory=dict)
    selected_manual_board_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "sources": list(self.sources),
            "business_configuration_sha256": (
                self.business_configuration_sha256
            ),
            "evidence_sha256": self.evidence_sha256,
            "visualization_sha256s": self.visualization_sha256s,
            "title": self.title,
            "company_name": self.company_name,
            "report_author": self.report_author,
            "business_objective": self.business_objective,
            "audience": self.audience,
            "tone": self.tone,
            "detail_level": self.detail_level,
            "user_notes": self.user_notes,
            "include_evidence_appendix": self.include_evidence_appendix,
            "selected_metric_ids": list(self.selected_metric_ids),
            "selected_evidence_ids": list(self.selected_evidence_ids),
            "selected_visualization_ids": list(
                self.selected_visualization_ids
            ),
            "manual_board_sha256s": self.manual_board_sha256s,
            "selected_manual_board_ids": list(self.selected_manual_board_ids),
        }


def validate_report_configuration(
    configuration: BusinessConfiguration,
    *,
    evidence_payload: dict[str, object] | None,
    visualizations: tuple[VisualizationArtifact, ...],
    title: object,
    business_objective: object,
    audience: object,
    tone: object,
    detail_level: object,
    user_notes: object,
    include_evidence_appendix: object,
    selected_metric_ids: list[str],
    selected_evidence_ids: list[str],
    selected_visualization_ids: list[str],
    company_name: object = "",
    report_author: object = "",
    manual_boards: tuple[ManualVisualizationArtifact, ...] = (),
    selected_manual_board_ids: list[str] | None = None,
) -> ReportConfiguration:
    """Validate report selections against current, revalidated artifacts."""

    if _DATASET_ID.fullmatch(configuration.dataset_id) is None:
        raise ReportConfigurationError("Report dataset ID is invalid.")
    report_title = _required_text(
        title,
        label="Report title",
        maximum=_MAX_TITLE_CHARACTERS,
    )
    selected_company_name = _optional_text(
        company_name,
        label="Company name",
        maximum=_MAX_BRANDING_CHARACTERS,
    )
    selected_report_author = _optional_text(
        report_author,
        label="Report author",
        maximum=_MAX_BRANDING_CHARACTERS,
    )
    objective = _required_text(
        business_objective,
        label="Business objective",
        maximum=_MAX_OBJECTIVE_CHARACTERS,
    )
    notes = _optional_text(
        user_notes,
        label="Report notes",
        maximum=_MAX_NOTES_CHARACTERS,
    )
    selected_audience = _choice(audience, AUDIENCES, "report audience")
    selected_tone = _choice(tone, TONES, "report tone")
    selected_detail = _choice(
        detail_level,
        DETAIL_LEVELS,
        "report detail level",
    )
    metric_ids = _unique_selection(
        selected_metric_ids,
        label="KPI",
        maximum=5,
    )
    if not metric_ids:
        raise ReportConfigurationError("Select at least one KPI for the report.")
    available_metrics = {
        metric.metric_id: metric for metric in configuration.metrics
    }
    if any(metric_id not in available_metrics for metric_id in metric_ids):
        raise ReportConfigurationError(
            "Report KPI selections must use configured KPIs."
        )
    ordered_metric_ids = tuple(
        metric.metric_id
        for metric in configuration.metrics
        if metric.metric_id in metric_ids
    )

    evidence_ids = _unique_selection(
        selected_evidence_ids,
        label="Evidence",
        maximum=_MAX_EVIDENCE_SELECTIONS,
    )
    evidence_records = _evidence_records(evidence_payload)
    evidence_by_id = {
        str(record["id"]): record
        for record in evidence_records
        if isinstance(record.get("id"), str)
    }
    if any(evidence_id not in evidence_by_id for evidence_id in evidence_ids):
        raise ReportConfigurationError(
            "Report evidence selections are unavailable or stale."
        )
    if any(
        (
            evidence_by_id[evidence_id].get("metric_id")
            != _DATASET_EVIDENCE_METRIC_ID
            and evidence_by_id[evidence_id].get("metric_id")
            not in ordered_metric_ids
        )
        for evidence_id in evidence_ids
    ):
        raise ReportConfigurationError(
            "Selected KPI evidence must belong to a selected KPI. "
            "Dataset-wide evidence may be selected independently."
        )
    ordered_evidence_ids = tuple(
        str(record["id"])
        for record in sorted(
            (
                evidence_by_id[evidence_id]
                for evidence_id in evidence_ids
            ),
            key=_evidence_sort_key,
        )
    )

    visualization_ids = _unique_selection(
        selected_visualization_ids,
        label="Visualization",
        maximum=_MAX_VISUALIZATION_SELECTIONS,
    )
    eligible_visualizations = {
        artifact.visualization_id: artifact
        for artifact in visualizations
        if artifact.visualization_id is not None
        and artifact.spec.include_in_report
    }
    if any(
        visualization_id not in eligible_visualizations
        for visualization_id in visualization_ids
    ):
        raise ReportConfigurationError(
            "Report visualizations must be saved and marked for report inclusion."
        )
    for visualization_id in visualization_ids:
        artifact = eligible_visualizations[visualization_id]
        referenced_metrics = {
            measure.selector.removeprefix("metric:")
            for measure in artifact.measures
            if measure.selector.startswith("metric:")
        }
        missing_metrics = referenced_metrics.difference(ordered_metric_ids)
        if missing_metrics:
            missing_names = ", ".join(
                available_metrics[metric_id].name
                for metric_id in sorted(missing_metrics)
                if metric_id in available_metrics
            )
            raise ReportConfigurationError(
                f'Visualization "{artifact.spec.title}" requires these '
                f"report KPIs: {missing_names}. Select the KPI(s) or exclude "
                "the visualization."
            )
    ordered_visualization_ids = tuple(sorted(visualization_ids))

    manual_board_ids = _unique_selection(
        selected_manual_board_ids or [],
        label="Manual board visualization",
        maximum=_MAX_VISUALIZATION_SELECTIONS,
    )
    eligible_manual_boards = {
        artifact.visualization_id: artifact for artifact in manual_boards
    }
    if any(
        visualization_id not in eligible_manual_boards
        for visualization_id in manual_board_ids
    ):
        raise ReportConfigurationError(
            "Report manual-board visualizations are unavailable or stale."
        )
    if any(
        eligible_manual_boards[visualization_id].png_filename is None
        for visualization_id in manual_board_ids
    ):
        raise ReportConfigurationError(
            "Reopen and save older manual-board visualizations before selecting "
            "them for a report. This creates the PNG required for PDF export."
        )
    ordered_manual_board_ids = tuple(sorted(manual_board_ids))

    sources = tuple(source.to_dict() for source in configuration.sources)
    if evidence_ids and (
        evidence_payload is None
        or evidence_payload.get("dataset_id") != configuration.dataset_id
        or evidence_payload.get("sources") != list(sources)
    ):
        raise ReportConfigurationError(
            "Selected evidence does not match the current report sources."
        )
    for visualization_id in ordered_visualization_ids:
        _validate_visualization_source(
            eligible_visualizations[visualization_id],
            sources=sources,
        )
    for visualization_id in ordered_manual_board_ids:
        if not any(
            eligible_manual_boards[visualization_id].source_sha256
            == source.get("sha256")
            for source in sources
        ):
            raise ReportConfigurationError(
                "Selected manual-board visualization does not match the current report sources."
            )

    return ReportConfiguration(
        schema_version=3,
        dataset_id=configuration.dataset_id,
        sources=sources,
        business_configuration_sha256=artifact_sha256(
            configuration.to_dict()
        ),
        evidence_sha256=(
            artifact_sha256(evidence_payload) if evidence_ids else None
        ),
        visualization_sha256s={
            visualization_id: artifact_sha256(
                eligible_visualizations[visualization_id].to_dict()
            )
            for visualization_id in ordered_visualization_ids
        },
        title=report_title,
        company_name=selected_company_name,
        report_author=selected_report_author,
        business_objective=objective,
        audience=selected_audience,
        tone=selected_tone,
        detail_level=selected_detail,
        user_notes=notes,
        include_evidence_appendix=_boolean(
            include_evidence_appendix
        ),
        selected_metric_ids=ordered_metric_ids,
        selected_evidence_ids=ordered_evidence_ids,
        selected_visualization_ids=ordered_visualization_ids,
        manual_board_sha256s={
            visualization_id: artifact_sha256(
                eligible_manual_boards[visualization_id].to_dict()
            )
            for visualization_id in ordered_manual_board_ids
        },
        selected_manual_board_ids=ordered_manual_board_ids,
    )


def save_report_configuration(
    report: ReportConfiguration,
    *,
    report_configuration_dir: Path,
) -> Path:
    """Atomically save one validated report selection outside static files."""

    report_configuration_dir.mkdir(parents=True, exist_ok=True)
    final_path = report_configuration_dir / f"{report.dataset_id}.json"
    temporary_path = report_configuration_dir / (
        f".{report.dataset_id}.{secrets.token_hex(8)}.part"
    )
    encoded = json.dumps(
        report.to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    try:
        temporary_path.write_text(f"{encoded}\n", encoding="utf-8")
        temporary_path.replace(final_path)
    except Exception as error:
        temporary_path.unlink(missing_ok=True)
        raise ReportConfigurationError(
            "Report configuration could not be saved."
        ) from error
    return final_path


def load_report_configuration(
    path: Path,
    *,
    configuration: BusinessConfiguration,
    evidence_payload: dict[str, object] | None,
    visualizations: tuple[VisualizationArtifact, ...],
    manual_boards: tuple[ManualVisualizationArtifact, ...] = (),
) -> ReportConfiguration:
    """Load and revalidate a saved report selection against current artifacts."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReportConfigurationError(
            "Saved report configuration is unreadable."
        ) from error
    if not isinstance(payload, dict):
        raise ReportConfigurationError(
            "Saved report configuration has an invalid shape."
        )
    schema_version = payload.get("schema_version")
    expected_keys = (
        _REPORT_KEYS_V1
        if schema_version == 1
        else _REPORT_KEYS_V2
        if schema_version == 2
        else _REPORT_KEYS_V3
        if schema_version == 3
        else frozenset()
    )
    if (
        set(payload) != expected_keys
        or payload.get("dataset_id") != configuration.dataset_id
    ):
        raise ReportConfigurationError(
            "Saved report configuration has an invalid shape."
        )
    selected_metric_ids = _string_list(
        payload.get("selected_metric_ids"),
        "saved KPI selections",
    )
    selected_evidence_ids = _string_list(
        payload.get("selected_evidence_ids"),
        "saved evidence selections",
    )
    selected_visualization_ids = _string_list(
        payload.get("selected_visualization_ids"),
        "saved visualization selections",
    )
    selected_manual_board_ids = _string_list(
        payload.get("selected_manual_board_ids", []),
        "saved manual-board visualization selections",
    )
    candidate = validate_report_configuration(
        configuration,
        evidence_payload=evidence_payload,
        visualizations=visualizations,
        title=payload.get("title"),
        business_objective=payload.get("business_objective"),
        audience=payload.get("audience"),
        tone=payload.get("tone"),
        detail_level=payload.get("detail_level"),
        user_notes=payload.get("user_notes"),
        include_evidence_appendix=payload.get(
            "include_evidence_appendix"
        ),
        selected_metric_ids=selected_metric_ids,
        selected_evidence_ids=selected_evidence_ids,
        selected_visualization_ids=selected_visualization_ids,
        company_name=payload.get("company_name", ""),
        report_author=payload.get("report_author", ""),
        manual_boards=manual_boards,
        selected_manual_board_ids=selected_manual_board_ids,
    )
    normalized_payload = dict(payload)
    if schema_version == 1:
        normalized_payload.update(
            {
                "schema_version": 3,
                "company_name": "",
                "report_author": "",
                "manual_board_sha256s": {},
                "selected_manual_board_ids": [],
            }
        )
    elif schema_version == 2:
        normalized_payload.update(
            {
                "schema_version": 3,
                "manual_board_sha256s": {},
                "selected_manual_board_ids": [],
            }
        )
    if candidate.to_dict() != normalized_payload:
        raise ReportConfigurationError(
            "Saved report configuration is stale or has been modified."
        )
    return candidate


def artifact_sha256(value: object) -> str:
    """Hash one JSON-compatible artifact using canonical serialization."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evidence_records(
    payload: dict[str, object] | None,
) -> tuple[dict[str, object], ...]:
    if payload is None:
        return ()
    records = payload.get("records")
    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        raise ReportConfigurationError(
            "Current evidence records are invalid."
        )
    record_ids: list[str] = []
    for record in records:
        evidence_id = record.get("id")
        metric_id = record.get("metric_id")
        ranking = record.get("ranking")
        if (
            not isinstance(evidence_id, str)
            or _EVIDENCE_ID.fullmatch(evidence_id) is None
            or not isinstance(metric_id, str)
            or not metric_id
            or not isinstance(ranking, dict)
            or not isinstance(ranking.get("rank"), int)
            or isinstance(ranking.get("rank"), bool)
            or ranking["rank"] < 1
        ):
            raise ReportConfigurationError(
                "Current evidence records are invalid."
            )
        record_ids.append(evidence_id)
    if len(record_ids) != len(set(record_ids)):
        raise ReportConfigurationError(
            "Current evidence records contain duplicate IDs."
        )
    return tuple(records)


def _evidence_sort_key(record: dict[str, object]) -> tuple[int, str]:
    ranking = record.get("ranking")
    rank = ranking.get("rank") if isinstance(ranking, dict) else None
    safe_rank = rank if isinstance(rank, int) and rank > 0 else 1_000_000
    return safe_rank, str(record.get("id", ""))


def _validate_visualization_source(
    artifact: VisualizationArtifact,
    *,
    sources: tuple[dict[str, object], ...],
) -> None:
    if not any(
        artifact.source.get("sha256") == source.get("sha256")
        and artifact.source.get("format") == source.get("format")
        and artifact.source.get("filename")
        == source.get("internal_filename")
        and artifact.source.get("worksheet") == source.get("table_name")
        for source in sources
    ):
        raise ReportConfigurationError(
            "Selected visualization does not match the current report sources."
        )


def _unique_selection(
    values: list[str],
    *,
    label: str,
    maximum: int,
) -> tuple[str, ...]:
    if not all(isinstance(value, str) and value for value in values):
        raise ReportConfigurationError(f"{label} selections are invalid.")
    if len(values) > maximum:
        raise ReportConfigurationError(
            f"Select at most {maximum} {label.lower()} items."
        )
    if len(values) != len(set(values)):
        raise ReportConfigurationError(
            f"{label} selections must not contain duplicates."
        )
    return tuple(values)


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ReportConfigurationError(f"{label.capitalize()} are invalid.")
    return list(value)


def _required_text(value: object, *, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportConfigurationError(f"{label} is required.")
    text = value.strip()
    if len(text) > maximum:
        raise ReportConfigurationError(
            f"{label} must be at most {maximum} characters."
        )
    return text


def _optional_text(value: object, *, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ReportConfigurationError(f"{label} is invalid.")
    text = value.strip()
    if len(text) > maximum:
        raise ReportConfigurationError(
            f"{label} must be at most {maximum} characters."
        )
    return text


def _choice(value: object, choices: tuple[str, ...], label: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ReportConfigurationError(f"Selected {label} is invalid.")
    return value


def _boolean(value: object) -> bool:
    return value is True or (
        isinstance(value, str) and value in {"yes", "true", "1", "on"}
    )
