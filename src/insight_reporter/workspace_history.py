"""Persistent workspace identity and recoverable local lifecycle management."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from insight_reporter.dataset_ingestion import DatasetUploadResult

_DATASET_ID = re.compile(r"[0-9a-f]{32}")
_REPORT_ID = re.compile(r"RPT-[0-9A-F]{16}")
_REPORT_FILENAME = re.compile(r"V([0-9]{4})-(RPT-[0-9A-F]{16})\.json")
_SOURCE_EXTENSIONS = frozenset({"csv", "json", "xlsx"})
_MAX_WORKSPACE_NAME_CHARACTERS = 120
_MAX_WORKSPACE_DESCRIPTION_CHARACTERS = 1_000
_MAX_ORIGINAL_FILENAME_CHARACTERS = 255
_MAX_REPORT_NAME_CHARACTERS = 160


class WorkspaceHistoryError(ValueError):
    """Raised when persistent workspace state is unsafe or unreadable."""


@dataclass(frozen=True)
class WorkspaceDirectories:
    """Filesystem locations required to reconstruct workspace progress."""

    upload_dir: Path
    workspace_dir: Path
    configuration_dir: Path
    insight_dir: Path
    evidence_dir: Path
    visualization_dir: Path
    visualization_insight_dir: Path
    report_configuration_dir: Path
    report_package_dir: Path
    generated_report_dir: Path
    trash_dir: Path


@dataclass(frozen=True)
class WorkspaceRecord:
    """Durable workspace identity with optional single-source metadata."""

    schema_version: int
    dataset_id: str
    name: str
    description: str
    original_filename: str | None
    internal_filename: str | None
    source_format: str | None
    source_sha256: str | None
    size_bytes: int | None
    created_at: str
    updated_at: str
    archived_at: str | None
    source_archived_at: str | None
    report_names: dict[str, str]
    archived_report_ids: tuple[str, ...]

    @property
    def has_source(self) -> bool:
        return self.internal_filename is not None and self.source_archived_at is None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "name": self.name,
            "description": self.description,
            "original_filename": self.original_filename,
            "internal_filename": self.internal_filename,
            "source_format": self.source_format,
            "source_sha256": self.source_sha256,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
            "source_archived_at": self.source_archived_at,
            "report_names": self.report_names,
            "archived_report_ids": list(self.archived_report_ids),
        }


@dataclass(frozen=True)
class WorkspaceSummary:
    """Derived workflow state and report counts for one workspace."""

    record: WorkspaceRecord
    stage: str
    stage_label: str
    last_activity_at: str
    report_version_count: int
    report_run_count: int
    archived_report_run_count: int
    metadata_warning: str | None = None


def create_empty_workspace(
    name: object,
    *,
    description: object = "",
    workspace_dir: Path,
) -> WorkspaceRecord:
    """Create a workspace identity before a source is selected."""

    workspace_dir.mkdir(parents=True, exist_ok=True)
    dataset_id = secrets.token_hex(16)
    while (workspace_dir / f"{dataset_id}.json").exists():
        dataset_id = secrets.token_hex(16)
    now = datetime.now(UTC).isoformat()
    record = WorkspaceRecord(
        schema_version=2,
        dataset_id=dataset_id,
        name=_clean_workspace_name(name),
        description=_clean_workspace_description(description),
        original_filename=None,
        internal_filename=None,
        source_format=None,
        source_sha256=None,
        size_bytes=None,
        created_at=now,
        updated_at=now,
        archived_at=None,
        source_archived_at=None,
        report_names={},
        archived_report_ids=(),
    )
    _save_workspace_record(record, workspace_dir=workspace_dir)
    return record


def create_workspace_record(
    upload: DatasetUploadResult,
    *,
    original_filename: str | None,
    workspace_dir: Path,
) -> WorkspaceRecord:
    """Create a source-backed workspace for the legacy upload-first route."""

    dataset_id = Path(upload.internal_filename).stem
    _validate_dataset_id(dataset_id)
    cleaned_filename = _clean_original_filename(original_filename)
    now = datetime.now(UTC).isoformat()
    record = WorkspaceRecord(
        schema_version=2,
        dataset_id=dataset_id,
        name=_workspace_name(cleaned_filename, dataset_id),
        description="",
        original_filename=cleaned_filename,
        internal_filename=upload.internal_filename,
        source_format=_validated_source_format(upload.source_format),
        source_sha256=_validated_source_sha256(upload.sha256),
        size_bytes=_validated_source_size(upload.size_bytes),
        created_at=now,
        updated_at=now,
        archived_at=None,
        source_archived_at=None,
        report_names={},
        archived_report_ids=(),
    )
    _save_workspace_record(record, workspace_dir=workspace_dir)
    return record


def attach_workspace_source(
    dataset_id: str,
    upload: DatasetUploadResult,
    *,
    original_filename: str | None,
    workspace_dir: Path,
) -> WorkspaceRecord:
    """Attach the first retained source to an existing empty workspace."""

    record = _required_workspace_record(
        dataset_id,
        workspace_dir=workspace_dir,
    )
    if record.is_archived:
        raise WorkspaceHistoryError("Restore the workspace before adding a data source.")
    if record.has_source:
        raise WorkspaceHistoryError("This workspace already has a data source.")
    if record.source_archived_at is not None:
        raise WorkspaceHistoryError("Restore the archived data source before replacing it.")
    if Path(upload.internal_filename).stem != dataset_id:
        raise WorkspaceHistoryError("Uploaded source does not belong to this workspace.")
    updated = replace(
        record,
        schema_version=2,
        original_filename=_clean_original_filename(original_filename),
        internal_filename=upload.internal_filename,
        source_format=_validated_source_format(upload.source_format),
        source_sha256=_validated_source_sha256(upload.sha256),
        size_bytes=_validated_source_size(upload.size_bytes),
        updated_at=datetime.now(UTC).isoformat(),
    )
    _save_workspace_record(updated, workspace_dir=workspace_dir)
    return updated


def update_workspace_details(
    dataset_id: str,
    *,
    name: object,
    description: object,
    workspace_dir: Path,
    fallback_record: WorkspaceRecord | None = None,
) -> WorkspaceRecord:
    """Edit user-facing workspace metadata without changing identity."""

    record = _workspace_record_or_fallback(
        dataset_id,
        workspace_dir=workspace_dir,
        fallback_record=fallback_record,
    )
    _require_active_workspace(record)
    updated = replace(
        record,
        schema_version=2,
        name=_clean_workspace_name(name),
        description=_clean_workspace_description(description),
        updated_at=datetime.now(UTC).isoformat(),
    )
    _save_workspace_record(updated, workspace_dir=workspace_dir)
    return updated


def rename_workspace(
    dataset_id: str,
    name: object,
    *,
    workspace_dir: Path,
    fallback_record: WorkspaceRecord | None = None,
) -> WorkspaceRecord:
    """Backward-compatible workspace-name update."""

    record = _workspace_record_or_fallback(
        dataset_id,
        workspace_dir=workspace_dir,
        fallback_record=fallback_record,
    )
    return update_workspace_details(
        dataset_id,
        name=name,
        description=record.description,
        workspace_dir=workspace_dir,
        fallback_record=record,
    )


def rename_workspace_source(
    dataset_id: str,
    name: object,
    *,
    workspace_dir: Path,
) -> WorkspaceRecord:
    """Change the source's presentation label without renaming its safe file."""

    record = _required_workspace_record(
        dataset_id,
        workspace_dir=workspace_dir,
    )
    _require_active_workspace(record)
    if not record.has_source:
        raise WorkspaceHistoryError("This workspace has no active data source.")
    updated = replace(
        record,
        schema_version=2,
        original_filename=_clean_original_filename(name if isinstance(name, str) else None),
        updated_at=datetime.now(UTC).isoformat(),
    )
    _save_workspace_record(updated, workspace_dir=workspace_dir)
    return updated


def archive_workspace(
    dataset_id: str,
    *,
    workspace_dir: Path,
) -> WorkspaceRecord:
    """Soft-delete a workspace while retaining every dependent artifact."""

    record = _required_workspace_record(
        dataset_id,
        workspace_dir=workspace_dir,
    )
    if record.is_archived:
        raise WorkspaceHistoryError("This workspace is already archived.")
    updated = replace(
        record,
        schema_version=2,
        archived_at=datetime.now(UTC).isoformat(),
        updated_at=datetime.now(UTC).isoformat(),
    )
    _save_workspace_record(updated, workspace_dir=workspace_dir)
    return updated


def restore_workspace(
    dataset_id: str,
    *,
    workspace_dir: Path,
) -> WorkspaceRecord:
    """Restore a soft-deleted workspace and all retained relationships."""

    record = _required_workspace_record(
        dataset_id,
        workspace_dir=workspace_dir,
    )
    if not record.is_archived:
        raise WorkspaceHistoryError("This workspace is not archived.")
    updated = replace(
        record,
        schema_version=2,
        archived_at=None,
        updated_at=datetime.now(UTC).isoformat(),
    )
    _save_workspace_record(updated, workspace_dir=workspace_dir)
    return updated


def archive_workspace_source(
    dataset_id: str,
    *,
    workspace_dir: Path,
    upload_dir: Path,
    trash_dir: Path,
) -> WorkspaceRecord:
    """Move the active source to recoverable trash while retaining reports."""

    record = _required_workspace_record(
        dataset_id,
        workspace_dir=workspace_dir,
    )
    _require_active_workspace(record)
    if not record.has_source or record.internal_filename is None:
        raise WorkspaceHistoryError("This workspace has no active data source.")
    source_path = upload_dir / record.internal_filename
    if not source_path.is_file():
        raise WorkspaceHistoryError("The retained data source is unavailable.")
    source_trash_dir = trash_dir / "sources" / dataset_id
    source_trash_dir.mkdir(parents=True, exist_ok=True)
    destination = source_trash_dir / source_path.name
    selection = upload_dir / f"{dataset_id}.selection.json"
    selection_destination = source_trash_dir / selection.name
    if destination.exists() or (selection.is_file() and selection_destination.exists()):
        raise WorkspaceHistoryError("A recoverable copy of this data source already exists.")
    selection_moved = False
    try:
        source_path.replace(destination)
        if selection.is_file():
            selection.replace(selection_destination)
            selection_moved = True
    except OSError as error:
        if selection_moved and selection_destination.is_file():
            selection_destination.replace(selection)
        if destination.is_file() and not source_path.exists():
            destination.replace(source_path)
        raise WorkspaceHistoryError(
            "The data source could not be moved to recoverable trash."
        ) from error
    updated = replace(
        record,
        schema_version=2,
        source_archived_at=datetime.now(UTC).isoformat(),
        updated_at=datetime.now(UTC).isoformat(),
    )
    try:
        _save_workspace_record(updated, workspace_dir=workspace_dir)
    except WorkspaceHistoryError:
        if selection_moved and selection_destination.is_file():
            selection_destination.replace(selection)
        if destination.is_file() and not source_path.exists():
            destination.replace(source_path)
        raise
    return updated


def restore_workspace_source(
    dataset_id: str,
    *,
    workspace_dir: Path,
    upload_dir: Path,
    trash_dir: Path,
) -> WorkspaceRecord:
    """Restore a source from recoverable trash to its original safe path."""

    record = _required_workspace_record(
        dataset_id,
        workspace_dir=workspace_dir,
    )
    _require_active_workspace(record)
    if record.source_archived_at is None or record.internal_filename is None:
        raise WorkspaceHistoryError("This workspace has no archived data source.")
    source_trash_dir = trash_dir / "sources" / dataset_id
    source_path = source_trash_dir / record.internal_filename
    destination = upload_dir / record.internal_filename
    selection = source_trash_dir / f"{dataset_id}.selection.json"
    selection_destination = upload_dir / selection.name
    if (
        not source_path.is_file()
        or destination.exists()
        or (selection.is_file() and selection_destination.exists())
    ):
        raise WorkspaceHistoryError("The archived data source cannot be restored safely.")
    upload_dir.mkdir(parents=True, exist_ok=True)
    selection_moved = False
    try:
        source_path.replace(destination)
        if selection.is_file():
            selection.replace(selection_destination)
            selection_moved = True
    except OSError as error:
        if selection_moved and selection_destination.is_file():
            selection_destination.replace(selection)
        if destination.is_file() and not source_path.exists():
            destination.replace(source_path)
        raise WorkspaceHistoryError("The archived data source could not be restored.") from error
    updated = replace(
        record,
        schema_version=2,
        source_archived_at=None,
        updated_at=datetime.now(UTC).isoformat(),
    )
    try:
        _save_workspace_record(updated, workspace_dir=workspace_dir)
    except WorkspaceHistoryError:
        if selection_moved and selection_destination.is_file():
            selection_destination.replace(selection)
        if destination.is_file() and not source_path.exists():
            destination.replace(source_path)
        raise
    if source_trash_dir.is_dir() and not tuple(source_trash_dir.iterdir()):
        source_trash_dir.rmdir()
    return updated


def rename_workspace_report(
    dataset_id: str,
    report_id: str,
    name: object,
    *,
    workspace_dir: Path,
) -> WorkspaceRecord:
    """Store a mutable display alias without editing immutable report JSON."""

    _validate_report_id(report_id)
    record = _required_workspace_record(
        dataset_id,
        workspace_dir=workspace_dir,
    )
    _require_active_workspace(record)
    names = dict(record.report_names)
    names[report_id] = _clean_report_name(name)
    updated = replace(
        record,
        schema_version=2,
        report_names=names,
        updated_at=datetime.now(UTC).isoformat(),
    )
    _save_workspace_record(updated, workspace_dir=workspace_dir)
    return updated


def archive_workspace_report(
    dataset_id: str,
    report_id: str,
    *,
    workspace_dir: Path,
) -> WorkspaceRecord:
    """Soft-delete one report run without changing its immutable versions."""

    _validate_report_id(report_id)
    record = _required_workspace_record(
        dataset_id,
        workspace_dir=workspace_dir,
    )
    _require_active_workspace(record)
    if report_id in record.archived_report_ids:
        raise WorkspaceHistoryError("This report is already archived.")
    archived = tuple(dict.fromkeys((*record.archived_report_ids, report_id)))
    updated = replace(
        record,
        schema_version=2,
        archived_report_ids=archived,
        updated_at=datetime.now(UTC).isoformat(),
    )
    _save_workspace_record(updated, workspace_dir=workspace_dir)
    return updated


def restore_workspace_report(
    dataset_id: str,
    report_id: str,
    *,
    workspace_dir: Path,
) -> WorkspaceRecord:
    """Restore one soft-deleted report run."""

    _validate_report_id(report_id)
    record = _required_workspace_record(
        dataset_id,
        workspace_dir=workspace_dir,
    )
    _require_active_workspace(record)
    if report_id not in record.archived_report_ids:
        raise WorkspaceHistoryError("This report is not archived.")
    updated = replace(
        record,
        schema_version=2,
        archived_report_ids=tuple(item for item in record.archived_report_ids if item != report_id),
        updated_at=datetime.now(UTC).isoformat(),
    )
    _save_workspace_record(updated, workspace_dir=workspace_dir)
    return updated


def load_workspace_record(
    dataset_id: str,
    *,
    workspace_dir: Path,
) -> WorkspaceRecord | None:
    """Load and validate one path-safe workspace metadata artifact."""

    _validate_dataset_id(dataset_id)
    path = workspace_dir / f"{dataset_id}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise WorkspaceHistoryError("Workspace metadata is unreadable.") from error
    return _parse_workspace_record(payload, expected_dataset_id=dataset_id)


def get_workspace_summary(
    dataset_id: str,
    *,
    directories: WorkspaceDirectories,
) -> WorkspaceSummary | None:
    """Reconstruct one workspace from metadata and retained artifacts."""

    _validate_dataset_id(dataset_id)
    warning = None
    try:
        record = load_workspace_record(
            dataset_id,
            workspace_dir=directories.workspace_dir,
        )
    except WorkspaceHistoryError:
        record = None
        warning = "Saved workspace metadata is invalid; safe source metadata is shown instead."
    source_path = _source_path(directories.upload_dir, dataset_id)
    if record is None:
        if source_path is None:
            return None
        record = _legacy_workspace_record(source_path)
        if warning is None:
            warning = "This dataset predates Milestone 6A, so its original filename is unavailable."
    elif record.has_source:
        if source_path is None:
            warning = "Workspace metadata references a source that is unavailable."
        elif record.internal_filename != source_path.name:
            warning = "Saved workspace metadata does not match the retained source."
    elif source_path is not None:
        warning = "A retained source exists but is not attached to this workspace."

    versions, runs, archived_runs = _report_counts(
        directories.generated_report_dir,
        dataset_id,
        archived_report_ids=set(record.archived_report_ids),
    )
    stage, stage_label = _workspace_stage(
        dataset_id,
        record=record,
        directories=directories,
        report_version_count=versions,
    )
    return WorkspaceSummary(
        record=record,
        stage=stage,
        stage_label=stage_label,
        last_activity_at=_last_activity(
            dataset_id,
            source_path=source_path,
            directories=directories,
        ),
        report_version_count=versions,
        report_run_count=runs,
        archived_report_run_count=archived_runs,
        metadata_warning=warning,
    )


def list_workspace_summaries(
    *,
    directories: WorkspaceDirectories,
    include_archived: bool = False,
) -> tuple[WorkspaceSummary, ...]:
    """Return workspace summaries ordered by most recent activity."""

    dataset_ids: set[str] = set()
    if directories.workspace_dir.is_dir():
        dataset_ids.update(
            path.stem
            for path in directories.workspace_dir.iterdir()
            if path.is_file()
            and path.suffix == ".json"
            and _DATASET_ID.fullmatch(path.stem) is not None
        )
    if directories.upload_dir.is_dir():
        dataset_ids.update(
            path.stem
            for path in directories.upload_dir.iterdir()
            if path.is_file()
            and path.suffix.removeprefix(".") in _SOURCE_EXTENSIONS
            and _DATASET_ID.fullmatch(path.stem) is not None
        )
    summaries = tuple(
        summary
        for dataset_id in dataset_ids
        if (
            summary := get_workspace_summary(
                dataset_id,
                directories=directories,
            )
        )
        is not None
        and (include_archived or not summary.record.is_archived)
    )
    return tuple(
        sorted(
            summaries,
            key=lambda summary: (
                summary.last_activity_at,
                summary.record.dataset_id,
            ),
            reverse=True,
        )
    )


def _workspace_stage(
    dataset_id: str,
    *,
    record: WorkspaceRecord,
    directories: WorkspaceDirectories,
    report_version_count: int,
) -> tuple[str, str]:
    if record.is_archived:
        return "archived", "Workspace archived"
    if not record.has_source:
        if record.source_archived_at is not None:
            return "source_archived", "Data source archived"
        return "source_required", "No data source selected"
    if report_version_count:
        return "report_generated", "Report generated"
    if (directories.report_package_dir / f"{dataset_id}.json").is_file() or (
        directories.report_configuration_dir / f"{dataset_id}.json"
    ).is_file():
        return "report_configured", "Report configured"
    if (directories.evidence_dir / f"{dataset_id}.json").is_file() or (
        directories.insight_dir / f"{dataset_id}.json"
    ).is_file():
        return "insights_generated", "Insights generated"
    if (directories.configuration_dir / f"{dataset_id}.json").is_file():
        return "kpis_configured", "KPIs configured"
    return "uploaded", "Dataset uploaded"


def _save_workspace_record(
    record: WorkspaceRecord,
    *,
    workspace_dir: Path,
) -> Path:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    final_path = workspace_dir / f"{record.dataset_id}.json"
    temporary_path = workspace_dir / (f".{record.dataset_id}.{secrets.token_hex(8)}.part")
    encoded = json.dumps(
        record.to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    try:
        temporary_path.write_text(f"{encoded}\n", encoding="utf-8")
        temporary_path.replace(final_path)
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise WorkspaceHistoryError("Workspace metadata could not be saved.") from error
    return final_path


def _parse_workspace_record(
    payload: object,
    *,
    expected_dataset_id: str,
) -> WorkspaceRecord:
    if not isinstance(payload, dict):
        raise WorkspaceHistoryError("Workspace metadata has an invalid schema.")
    if payload.get("schema_version") == 1:
        return _parse_legacy_workspace_record(
            payload,
            expected_dataset_id=expected_dataset_id,
        )
    expected_fields = {
        "schema_version",
        "dataset_id",
        "name",
        "description",
        "original_filename",
        "internal_filename",
        "source_format",
        "source_sha256",
        "size_bytes",
        "created_at",
        "updated_at",
        "archived_at",
        "source_archived_at",
        "report_names",
        "archived_report_ids",
    }
    if set(payload) != expected_fields or payload.get("schema_version") != 2:
        raise WorkspaceHistoryError("Workspace metadata version is unsupported.")
    dataset_id = _validated_record_identity(
        payload.get("dataset_id"),
        expected_dataset_id=expected_dataset_id,
    )
    internal_filename = payload.get("internal_filename")
    raw_original_filename = payload.get("original_filename")
    source_format = payload.get("source_format")
    source_sha256 = payload.get("source_sha256")
    size_bytes = payload.get("size_bytes")
    source_values = (
        internal_filename,
        source_format,
        source_sha256,
        size_bytes,
    )
    if any(value is not None for value in source_values):
        if any(value is None for value in source_values):
            raise WorkspaceHistoryError("Workspace source metadata is incomplete.")
        source_format = _validated_source_format(source_format)
        if internal_filename != f"{dataset_id}.{source_format}":
            raise WorkspaceHistoryError("Workspace source identity is invalid.")
        source_sha256 = _validated_source_sha256(source_sha256)
        size_bytes = _validated_source_size(size_bytes)
        if not isinstance(raw_original_filename, str):
            raise WorkspaceHistoryError("Workspace source label is invalid.")
        original_filename = _clean_original_filename(raw_original_filename)
    else:
        if raw_original_filename is not None or payload.get("source_archived_at") is not None:
            raise WorkspaceHistoryError("Workspace source metadata is incomplete.")
        original_filename = None
    report_names = _validated_report_names(payload.get("report_names"))
    archived_report_ids = _validated_report_ids(payload.get("archived_report_ids"))
    return WorkspaceRecord(
        schema_version=2,
        dataset_id=dataset_id,
        name=_clean_workspace_name(payload.get("name")),
        description=_clean_workspace_description(payload.get("description")),
        original_filename=original_filename,
        internal_filename=internal_filename,
        source_format=source_format,
        source_sha256=source_sha256,
        size_bytes=size_bytes,
        created_at=_validated_timestamp(payload.get("created_at")),
        updated_at=_validated_timestamp(payload.get("updated_at")),
        archived_at=_optional_timestamp(payload.get("archived_at")),
        source_archived_at=_optional_timestamp(payload.get("source_archived_at")),
        report_names=report_names,
        archived_report_ids=archived_report_ids,
    )


def _parse_legacy_workspace_record(
    payload: dict[str, object],
    *,
    expected_dataset_id: str,
) -> WorkspaceRecord:
    expected_fields = {
        "schema_version",
        "dataset_id",
        "name",
        "original_filename",
        "internal_filename",
        "source_format",
        "source_sha256",
        "size_bytes",
        "created_at",
    }
    if set(payload) != expected_fields:
        raise WorkspaceHistoryError("Workspace metadata has an invalid schema.")
    dataset_id = _validated_record_identity(
        payload.get("dataset_id"),
        expected_dataset_id=expected_dataset_id,
    )
    source_format = _validated_source_format(payload.get("source_format"))
    if payload.get("internal_filename") != f"{dataset_id}.{source_format}":
        raise WorkspaceHistoryError("Workspace source identity is invalid.")
    created_at = _validated_timestamp(payload.get("created_at"))
    return WorkspaceRecord(
        schema_version=1,
        dataset_id=dataset_id,
        name=_clean_workspace_name(payload.get("name")),
        description="",
        original_filename=_clean_original_filename(
            payload.get("original_filename")
            if isinstance(payload.get("original_filename"), str)
            else None
        ),
        internal_filename=f"{dataset_id}.{source_format}",
        source_format=source_format,
        source_sha256=_validated_source_sha256(payload.get("source_sha256")),
        size_bytes=_validated_source_size(payload.get("size_bytes")),
        created_at=created_at,
        updated_at=created_at,
        archived_at=None,
        source_archived_at=None,
        report_names={},
        archived_report_ids=(),
    )


def _workspace_record_or_fallback(
    dataset_id: str,
    *,
    workspace_dir: Path,
    fallback_record: WorkspaceRecord | None,
) -> WorkspaceRecord:
    try:
        record = load_workspace_record(
            dataset_id,
            workspace_dir=workspace_dir,
        )
    except WorkspaceHistoryError:
        record = None
    if record is None and fallback_record is not None:
        if fallback_record.dataset_id != dataset_id:
            raise WorkspaceHistoryError("Workspace fallback identity is invalid.")
        record = fallback_record
    if record is None:
        raise WorkspaceHistoryError("Workspace metadata is unavailable.")
    return record


def _required_workspace_record(
    dataset_id: str,
    *,
    workspace_dir: Path,
) -> WorkspaceRecord:
    record = load_workspace_record(
        dataset_id,
        workspace_dir=workspace_dir,
    )
    if record is None:
        raise WorkspaceHistoryError("Workspace metadata is unavailable.")
    return record


def _require_active_workspace(record: WorkspaceRecord) -> None:
    if record.is_archived:
        raise WorkspaceHistoryError("Restore the workspace before changing its contents.")


def _legacy_workspace_record(source_path: Path) -> WorkspaceRecord:
    dataset_id = source_path.stem
    modified = datetime.fromtimestamp(
        source_path.stat().st_mtime,
        UTC,
    ).isoformat()
    return WorkspaceRecord(
        schema_version=1,
        dataset_id=dataset_id,
        name=f"Dataset {dataset_id[:8]}",
        description="",
        original_filename="Original filename unavailable",
        internal_filename=source_path.name,
        source_format=source_path.suffix.removeprefix("."),
        source_sha256=_file_sha256(source_path),
        size_bytes=source_path.stat().st_size,
        created_at=modified,
        updated_at=modified,
        archived_at=None,
        source_archived_at=None,
        report_names={},
        archived_report_ids=(),
    )


def _last_activity(
    dataset_id: str,
    *,
    source_path: Path | None,
    directories: WorkspaceDirectories,
) -> str:
    paths = [
        directories.workspace_dir / f"{dataset_id}.json",
        directories.configuration_dir / f"{dataset_id}.json",
        directories.insight_dir / f"{dataset_id}.json",
        directories.evidence_dir / f"{dataset_id}.json",
        directories.report_configuration_dir / f"{dataset_id}.json",
        directories.report_package_dir / f"{dataset_id}.json",
    ]
    if source_path is not None:
        paths.append(source_path)
    for directory in (
        directories.visualization_dir / dataset_id,
        directories.visualization_insight_dir / dataset_id,
        directories.generated_report_dir / dataset_id,
    ):
        if directory.is_dir():
            paths.extend(path for path in directory.iterdir() if path.is_file())
    existing = [path.stat().st_mtime for path in paths if path.is_file()]
    if not existing:
        return datetime.now(UTC).isoformat()
    return datetime.fromtimestamp(max(existing), UTC).isoformat()


def _report_counts(
    generated_report_dir: Path,
    dataset_id: str,
    *,
    archived_report_ids: set[str],
) -> tuple[int, int, int]:
    dataset_dir = generated_report_dir / dataset_id
    if not dataset_dir.is_dir():
        return 0, 0, 0
    matches = [
        match
        for path in dataset_dir.iterdir()
        if path.is_file() and (match := _REPORT_FILENAME.fullmatch(path.name)) is not None
    ]
    active = [match for match in matches if match.group(2) not in archived_report_ids]
    archived_runs = {match.group(2) for match in matches if match.group(2) in archived_report_ids}
    return (
        len(active),
        len({match.group(2) for match in active}),
        len(archived_runs),
    )


def _source_path(upload_dir: Path, dataset_id: str) -> Path | None:
    matches = tuple(
        path
        for extension in _SOURCE_EXTENSIONS
        if (path := upload_dir / f"{dataset_id}.{extension}").is_file()
    )
    if len(matches) > 1:
        raise WorkspaceHistoryError("Workspace source identity is ambiguous.")
    return matches[0] if matches else None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise WorkspaceHistoryError(
            "Workspace source fingerprint could not be calculated."
        ) from error
    return digest.hexdigest()


def _clean_original_filename(value: str | None) -> str:
    if not value:
        return "Unnamed dataset"
    basename = re.split(r"[/\\]", value)[-1].strip()
    cleaned = "".join(character if character.isprintable() else " " for character in basename)
    cleaned = " ".join(cleaned.split())
    return (cleaned or "Unnamed dataset")[:_MAX_ORIGINAL_FILENAME_CHARACTERS]


def _workspace_name(original_filename: str, dataset_id: str) -> str:
    candidate = Path(original_filename).stem.strip()
    return _clean_workspace_name(candidate or f"Dataset {dataset_id[:8]}")


def _clean_workspace_name(value: object) -> str:
    return _clean_bounded_text(
        value,
        label="Workspace name",
        maximum=_MAX_WORKSPACE_NAME_CHARACTERS,
        allow_empty=False,
    )


def _clean_workspace_description(value: object) -> str:
    return _clean_bounded_text(
        value,
        label="Workspace description",
        maximum=_MAX_WORKSPACE_DESCRIPTION_CHARACTERS,
        allow_empty=True,
    )


def _clean_report_name(value: object) -> str:
    return _clean_bounded_text(
        value,
        label="Report name",
        maximum=_MAX_REPORT_NAME_CHARACTERS,
        allow_empty=False,
    )


def _clean_bounded_text(
    value: object,
    *,
    label: str,
    maximum: int,
    allow_empty: bool,
) -> str:
    if not isinstance(value, str):
        raise WorkspaceHistoryError(f"{label} is required.")
    cleaned = " ".join(
        "".join(character if character.isprintable() else " " for character in value).split()
    )
    if not cleaned and not allow_empty:
        raise WorkspaceHistoryError(f"{label} is required.")
    if len(cleaned) > maximum:
        raise WorkspaceHistoryError(f"{label} must be at most {maximum} characters.")
    return cleaned


def _validated_record_identity(
    value: object,
    *,
    expected_dataset_id: str,
) -> str:
    if value != expected_dataset_id or not isinstance(value, str):
        raise WorkspaceHistoryError("Workspace metadata identity is invalid.")
    _validate_dataset_id(value)
    return value


def _validated_source_format(value: object) -> str:
    if not isinstance(value, str) or value not in _SOURCE_EXTENSIONS:
        raise WorkspaceHistoryError("Workspace source format is invalid.")
    return value


def _validated_source_sha256(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise WorkspaceHistoryError("Workspace source fingerprint is invalid.")
    return value


def _validated_source_size(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise WorkspaceHistoryError("Workspace source size is invalid.")
    return value


def _validated_report_names(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise WorkspaceHistoryError("Workspace report names are invalid.")
    names: dict[str, str] = {}
    for report_id, name in value.items():
        if not isinstance(report_id, str):
            raise WorkspaceHistoryError("Workspace report identity is invalid.")
        _validate_report_id(report_id)
        names[report_id] = _clean_report_name(name)
    return names


def _validated_report_ids(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) != len(set(value))
        or any(not isinstance(item, str) for item in value)
    ):
        raise WorkspaceHistoryError("Workspace archived reports are invalid.")
    for report_id in value:
        _validate_report_id(report_id)
    return tuple(value)


def _validated_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise WorkspaceHistoryError("Workspace timestamp is invalid.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise WorkspaceHistoryError("Workspace timestamp is invalid.") from error
    if parsed.tzinfo is None:
        raise WorkspaceHistoryError("Workspace timestamp is invalid.")
    return parsed.isoformat()


def _optional_timestamp(value: object) -> str | None:
    if value is None:
        return None
    return _validated_timestamp(value)


def _validate_dataset_id(dataset_id: str) -> None:
    if _DATASET_ID.fullmatch(dataset_id) is None:
        raise WorkspaceHistoryError("Workspace dataset ID is invalid.")


def _validate_report_id(report_id: str) -> None:
    if _REPORT_ID.fullmatch(report_id) is None:
        raise WorkspaceHistoryError("Workspace report ID is invalid.")
