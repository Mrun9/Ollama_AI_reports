"""Bounded, evidence-only input contract for future report narration."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from insight_reporter.business_config import BusinessConfiguration
from insight_reporter.manual_visualization_evidence import (
    ManualVisualizationEvidence,
    generate_manual_visualization_evidence,
)
from insight_reporter.report_configuration import (
    ReportConfiguration,
    artifact_sha256,
)
from insight_reporter.visualization_builder import VisualizationArtifact

_DATASET_ID = re.compile(r"[0-9a-f]{32}")
_MAX_EVIDENCE_RECORDS = 50
_MAX_EVIDENCE_SUPPORTING_ROWS = 12
_MAX_MANUAL_VISUALIZATIONS = 20
_MAX_PACKAGE_CHARACTERS = 250_000


class ReportGenerationPackageError(ValueError):
    """Raised when selected artifacts are not ready for safe narration."""


@dataclass(frozen=True)
class ReportGenerationPackage:
    schema_version: int
    dataset_id: str
    report_configuration_sha256: str
    report_settings: dict[str, object]
    sources: tuple[dict[str, object], ...]
    kpis: tuple[dict[str, object], ...]
    deterministic_evidence: tuple[dict[str, object], ...]
    manual_visualization_evidence: tuple[
        ManualVisualizationEvidence, ...
    ]
    omissions: dict[str, object]
    model_input_policy: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "report_configuration_sha256": (
                self.report_configuration_sha256
            ),
            "report_settings": self.report_settings,
            "sources": list(self.sources),
            "kpis": list(self.kpis),
            "deterministic_evidence": list(
                self.deterministic_evidence
            ),
            "manual_visualization_evidence": [
                evidence.to_dict()
                for evidence in self.manual_visualization_evidence
            ],
            "omissions": self.omissions,
            "model_input_policy": self.model_input_policy,
        }


def build_report_generation_package(
    report: ReportConfiguration,
    *,
    configuration: BusinessConfiguration,
    evidence_payload: dict[str, object] | None,
    visualizations: tuple[VisualizationArtifact, ...],
) -> ReportGenerationPackage:
    """Build the exact bounded JSON contract that Milestone 5B may narrate."""

    if (
        _DATASET_ID.fullmatch(report.dataset_id) is None
        or configuration.dataset_id != report.dataset_id
    ):
        raise ReportGenerationPackageError(
            "Report generation inputs belong to different datasets."
        )
    metrics = {
        metric.metric_id: metric for metric in configuration.metrics
    }
    selected_kpis = tuple(
        metrics[metric_id].to_dict()
        for metric_id in report.selected_metric_ids
        if metric_id in metrics
    )
    if len(selected_kpis) != len(report.selected_metric_ids):
        raise ReportGenerationPackageError(
            "A selected report KPI is no longer configured."
        )

    evidence_by_id = _evidence_by_id(evidence_payload)
    selected_records = [
        evidence_by_id[evidence_id]
        for evidence_id in report.selected_evidence_ids
        if evidence_id in evidence_by_id
    ]
    if len(selected_records) != len(report.selected_evidence_ids):
        raise ReportGenerationPackageError(
            "Selected deterministic evidence is unavailable. Regenerate "
            "deterministic insights and review the report selection."
        )
    for record in selected_records:
        if not isinstance(record.get("observation"), dict):
            raise ReportGenerationPackageError(
                "Selected evidence uses the previous schema. Regenerate "
                "deterministic insights before report generation."
            )
    included_records = selected_records[:_MAX_EVIDENCE_RECORDS]
    compact_evidence = tuple(
        _compact_evidence(record) for record in included_records
    )

    visualization_by_id = {
        artifact.visualization_id: artifact
        for artifact in visualizations
        if artifact.visualization_id is not None
    }
    selected_visualizations = [
        visualization_by_id[visualization_id]
        for visualization_id in report.selected_visualization_ids
        if visualization_id in visualization_by_id
    ]
    if len(selected_visualizations) != len(
        report.selected_visualization_ids
    ):
        raise ReportGenerationPackageError(
            "A selected manual visualization is unavailable or stale."
        )
    included_visualizations = selected_visualizations[
        :_MAX_MANUAL_VISUALIZATIONS
    ]
    manual_evidence = tuple(
        generate_manual_visualization_evidence(artifact)
        for artifact in included_visualizations
    )
    package = ReportGenerationPackage(
        schema_version=1,
        dataset_id=report.dataset_id,
        report_configuration_sha256=artifact_sha256(
            report.to_dict()
        ),
        report_settings={
            "title": report.title,
            "company_name": report.company_name,
            "report_author": report.report_author,
            "business_objective": report.business_objective,
            "audience": report.audience,
            "tone": report.tone,
            "detail_level": report.detail_level,
            "user_notes": {
                "content": report.user_notes,
                "source": "user_provided",
            },
            "include_evidence_appendix": (
                report.include_evidence_appendix
            ),
        },
        sources=report.sources,
        kpis=selected_kpis,
        deterministic_evidence=compact_evidence,
        manual_visualization_evidence=manual_evidence,
        omissions={
            "selected_evidence_record_count": len(selected_records),
            "included_evidence_record_count": len(included_records),
            "omitted_evidence_ids": [
                str(record["id"])
                for record in selected_records[_MAX_EVIDENCE_RECORDS:]
            ],
            "selected_manual_visualization_count": len(
                selected_visualizations
            ),
            "included_manual_visualization_count": len(
                included_visualizations
            ),
            "omitted_visualization_ids": [
                str(artifact.visualization_id)
                for artifact in selected_visualizations[
                    _MAX_MANUAL_VISUALIZATIONS:
                ]
            ],
        },
        model_input_policy={
            "raw_dataset_rows_included": False,
            "identifiers_included": False,
            "free_text_source_columns_included": False,
            "user_notes_label": "user_provided",
            "all_numbers_calculated_by": "python",
            "causal_claims_allowed": False,
            "unknown_evidence_ids_allowed": False,
            "unknown_visualization_ids_allowed": False,
            "maximum_evidence_records": _MAX_EVIDENCE_RECORDS,
            "maximum_supporting_rows_per_evidence": (
                _MAX_EVIDENCE_SUPPORTING_ROWS
            ),
            "maximum_manual_visualizations": (
                _MAX_MANUAL_VISUALIZATIONS
            ),
        },
    )
    encoded = json.dumps(
        package.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded) > _MAX_PACKAGE_CHARACTERS:
        raise ReportGenerationPackageError(
            "The selected report content exceeds the bounded narration "
            "package. Select fewer evidence records or visualizations."
        )
    return package


def save_report_generation_package(
    package: ReportGenerationPackage,
    *,
    package_dir: Path,
) -> Path:
    """Atomically save one package outside Flask static files."""

    if _DATASET_ID.fullmatch(package.dataset_id) is None:
        raise ReportGenerationPackageError(
            "Report package dataset ID is invalid."
        )
    package_dir.mkdir(parents=True, exist_ok=True)
    final_path = package_dir / f"{package.dataset_id}.json"
    temporary_path = package_dir / (
        f".{package.dataset_id}.{secrets.token_hex(8)}.part"
    )
    encoded = json.dumps(
        package.to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    try:
        temporary_path.write_text(f"{encoded}\n", encoding="utf-8")
        temporary_path.replace(final_path)
    except Exception as error:
        temporary_path.unlink(missing_ok=True)
        raise ReportGenerationPackageError(
            "Report generation package could not be saved."
        ) from error
    return final_path


def _evidence_by_id(
    evidence_payload: dict[str, object] | None,
) -> dict[str, dict[str, object]]:
    if evidence_payload is None:
        return {}
    records = evidence_payload.get("records")
    if not isinstance(records, list):
        raise ReportGenerationPackageError(
            "Deterministic evidence is invalid."
        )
    return {
        str(record["id"]): record
        for record in records
        if isinstance(record, dict) and isinstance(record.get("id"), str)
    }


def _compact_evidence(
    record: dict[str, object],
) -> dict[str, object]:
    supporting_data = record.get("supporting_data")
    rows = (
        [row for row in supporting_data if isinstance(row, dict)]
        if isinstance(supporting_data, list)
        else []
    )
    included_rows = [
        _strip_row_identity(row)
        for row in rows[:_MAX_EVIDENCE_SUPPORTING_ROWS]
    ]
    return {
        "id": record.get("id"),
        "insight_id": record.get("insight_id"),
        "insight_type": record.get("insight_type"),
        "metric_id": record.get("metric_id"),
        "metric": record.get("metric"),
        "kpi_definition": record.get("kpi_definition"),
        "source_columns": record.get("source_columns"),
        "filters": record.get("filters"),
        "periods": record.get("periods"),
        "calculation_description": record.get(
            "calculation_description"
        ),
        "observation": record.get("observation"),
        "record_count": record.get("record_count"),
        "ranking": record.get("ranking"),
        "limitations": record.get("limitations"),
        "chart": record.get("chart"),
        "supporting_data": included_rows,
        "supporting_data_omitted_count": max(
            0,
            len(rows) - len(included_rows),
        ),
    }


def _strip_row_identity(
    row: dict[str, object],
) -> dict[str, object]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"row", "row_id", "row_number", "record_id"}
    }
