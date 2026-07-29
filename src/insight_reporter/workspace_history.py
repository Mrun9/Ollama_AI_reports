"""Persistent workspace metadata and filesystem-backed workflow history."""

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
_REPORT_FILENAME = re.compile(r"V([0-9]{4})-(RPT-[0-9A-F]{16})\.json")
_SOURCE_EXTENSIONS = frozenset({"csv", "json", "xlsx"})
_MAX_WORKSPACE_NAME_CHARACTERS = 120
_MAX_ORIGINAL_FILENAME_CHARACTERS = 255


class WorkspaceHistoryError(ValueError):
    """Raised when persistent workspace metadata is unsafe or unreadable."""


@dataclass(frozen=True)
class WorkspaceDirectories:
    """Filesystem locations required to reconstruct workspace progress."""

    upload_dir: Path
    workspace_dir: Path
    configuration_dir: Path
    insight_dir: Path
    evidence_dir: Path
    visualization_dir: Path
    report_configuration_dir: Path
    report_package_dir: Path
    generated_report_dir: Path


@dataclass(frozen=True)
class WorkspaceRecord:
    """Durable, user-facing identity for one uploaded dataset."""

    schema_version: int
    dataset_id: str
    name: str
    original_filename: str
    internal_filename: str
    source_format: str
    source_sha256: str
    size_bytes: int
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "name": self.name,
            "original_filename": self.original_filename,
            "internal_filename": self.internal_filename,
            "source_format": self.source_format,
            "source_sha256": self.source_sha256,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class WorkspaceSummary:
    """Derived progress and report-history counts for one workspace."""

    record: WorkspaceRecord
    stage: str
    stage_label: str
    last_activity_at: str
    report_version_count: int
    report_run_count: int
    metadata_warning: str | None = None


def create_workspace_record(
    upload: DatasetUploadResult,
    *,
    original_filename: str | None,
    workspace_dir: Path,
) -> WorkspaceRecord:
    """Persist safe source metadata immediately after a successful upload."""

    dataset_id = Path(upload.internal_filename).stem
    if _DATASET_ID.fullmatch(dataset_id) is None:
        raise WorkspaceHistoryError("Workspace dataset ID is invalid.")
    if upload.source_format not in _SOURCE_EXTENSIONS:
        raise WorkspaceHistoryError("Workspace source format is invalid.")
    cleaned_filename = _clean_original_filename(original_filename)
    record = WorkspaceRecord(
        schema_version=1,
        dataset_id=dataset_id,
        name=_workspace_name(cleaned_filename, dataset_id),
        original_filename=cleaned_filename,
        internal_filename=upload.internal_filename,
        source_format=upload.source_format,
        source_sha256=upload.sha256,
        size_bytes=upload.size_bytes,
        created_at=datetime.now(UTC).isoformat(),
    )
    _save_workspace_record(record, workspace_dir=workspace_dir)
    return record


def rename_workspace(
    dataset_id: str,
    name: object,
    *,
    workspace_dir: Path,
    fallback_record: WorkspaceRecord | None = None,
) -> WorkspaceRecord:
    """Change only the human-readable name of an existing workspace."""

    try:
        record = load_workspace_record(
            dataset_id,
            workspace_dir=workspace_dir,
        )
    except WorkspaceHistoryError:
        record = None
    if record is None and fallback_record is not None:
        if fallback_record.dataset_id != dataset_id:
            raise WorkspaceHistoryError(
                "Workspace fallback identity is invalid."
            )
        record = fallback_record
    if record is None:
        raise WorkspaceHistoryError("Workspace metadata is unavailable.")
    updated = replace(record, name=_clean_workspace_name(name))
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
    """Reconstruct one workspace from its source and downstream artifacts."""

    _validate_dataset_id(dataset_id)
    source_path = _source_path(directories.upload_dir, dataset_id)
    if source_path is None:
        return None
    warning = None
    try:
        record = load_workspace_record(
            dataset_id,
            workspace_dir=directories.workspace_dir,
        )
    except WorkspaceHistoryError:
        record = None
        warning = (
            "Saved workspace metadata is invalid; safe source metadata is "
            "shown instead."
        )
    if record is None:
        record = _legacy_workspace_record(source_path)
        if warning is None:
            warning = (
                "This dataset predates Milestone 6A, so its original filename "
                "is unavailable."
            )
    elif record.internal_filename != source_path.name:
        record = _legacy_workspace_record(source_path)
        warning = (
            "Saved workspace metadata does not match the retained source; "
            "safe source metadata is shown instead."
        )

    report_versions, report_runs = _report_counts(
        directories.generated_report_dir,
        dataset_id,
    )
    stage, stage_label = _workspace_stage(
        dataset_id,
        directories=directories,
        report_version_count=report_versions,
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
        report_version_count=report_versions,
        report_run_count=report_runs,
        metadata_warning=warning,
    )


def list_workspace_summaries(
    *,
    directories: WorkspaceDirectories,
) -> tuple[WorkspaceSummary, ...]:
    """Return every retained dataset ordered by most recent activity."""

    dataset_ids = {
        path.stem
        for path in directories.upload_dir.iterdir()
        if path.is_file()
        and path.suffix.removeprefix(".") in _SOURCE_EXTENSIONS
        and _DATASET_ID.fullmatch(path.stem) is not None
    } if directories.upload_dir.is_dir() else set()
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


def _save_workspace_record(
    record: WorkspaceRecord,
    *,
    workspace_dir: Path,
) -> Path:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    final_path = workspace_dir / f"{record.dataset_id}.json"
    temporary_path = workspace_dir / (
        f".{record.dataset_id}.{secrets.token_hex(8)}.part"
    )
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
        raise WorkspaceHistoryError(
            "Workspace metadata could not be saved."
        ) from error
    return final_path


def _parse_workspace_record(
    payload: object,
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
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise WorkspaceHistoryError("Workspace metadata has an invalid schema.")
    if payload.get("schema_version") != 1:
        raise WorkspaceHistoryError("Workspace metadata version is unsupported.")
    dataset_id = payload.get("dataset_id")
    if dataset_id != expected_dataset_id or not isinstance(dataset_id, str):
        raise WorkspaceHistoryError("Workspace metadata identity is invalid.")
    _validate_dataset_id(dataset_id)
    source_format = payload.get("source_format")
    internal_filename = payload.get("internal_filename")
    if (
        not isinstance(source_format, str)
        or source_format not in _SOURCE_EXTENSIONS
        or internal_filename != f"{dataset_id}.{source_format}"
    ):
        raise WorkspaceHistoryError("Workspace source identity is invalid.")
    source_sha256 = payload.get("source_sha256")
    if (
        not isinstance(source_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
    ):
        raise WorkspaceHistoryError("Workspace source fingerprint is invalid.")
    size_bytes = payload.get("size_bytes")
    if (
        not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes <= 0
    ):
        raise WorkspaceHistoryError("Workspace source size is invalid.")
    created_at = _validated_timestamp(payload.get("created_at"))
    name = _clean_workspace_name(payload.get("name"))
    original_filename = _clean_original_filename(
        payload.get("original_filename")
        if isinstance(payload.get("original_filename"), str)
        else None
    )
    return WorkspaceRecord(
        schema_version=1,
        dataset_id=dataset_id,
        name=name,
        original_filename=original_filename,
        internal_filename=internal_filename,
        source_format=source_format,
        source_sha256=source_sha256,
        size_bytes=size_bytes,
        created_at=created_at,
    )


def _legacy_workspace_record(source_path: Path) -> WorkspaceRecord:
    dataset_id = source_path.stem
    modified = datetime.fromtimestamp(source_path.stat().st_mtime, UTC).isoformat()
    return WorkspaceRecord(
        schema_version=1,
        dataset_id=dataset_id,
        name=f"Dataset {dataset_id[:8]}",
        original_filename="Original filename unavailable",
        internal_filename=source_path.name,
        source_format=source_path.suffix.removeprefix("."),
        source_sha256=_file_sha256(source_path),
        size_bytes=source_path.stat().st_size,
        created_at=modified,
    )


def _workspace_stage(
    dataset_id: str,
    *,
    directories: WorkspaceDirectories,
    report_version_count: int,
) -> tuple[str, str]:
    if report_version_count:
        return "report_generated", "Report generated"
    if (
        directories.report_package_dir / f"{dataset_id}.json"
    ).is_file() or (
        directories.report_configuration_dir / f"{dataset_id}.json"
    ).is_file():
        return "report_configured", "Report configured"
    if (
        directories.evidence_dir / f"{dataset_id}.json"
    ).is_file() or (
        directories.insight_dir / f"{dataset_id}.json"
    ).is_file():
        return "insights_generated", "Insights generated"
    if (directories.configuration_dir / f"{dataset_id}.json").is_file():
        return "kpis_configured", "KPIs configured"
    return "uploaded", "Dataset uploaded"


def _last_activity(
    dataset_id: str,
    *,
    source_path: Path,
    directories: WorkspaceDirectories,
) -> str:
    paths = [
        source_path,
        directories.workspace_dir / f"{dataset_id}.json",
        directories.configuration_dir / f"{dataset_id}.json",
        directories.insight_dir / f"{dataset_id}.json",
        directories.evidence_dir / f"{dataset_id}.json",
        directories.report_configuration_dir / f"{dataset_id}.json",
        directories.report_package_dir / f"{dataset_id}.json",
    ]
    for directory in (
        directories.visualization_dir / dataset_id,
        directories.generated_report_dir / dataset_id,
    ):
        if directory.is_dir():
            paths.extend(path for path in directory.iterdir() if path.is_file())
    latest = max(path.stat().st_mtime for path in paths if path.is_file())
    return datetime.fromtimestamp(latest, UTC).isoformat()


def _report_counts(
    generated_report_dir: Path,
    dataset_id: str,
) -> tuple[int, int]:
    dataset_dir = generated_report_dir / dataset_id
    if not dataset_dir.is_dir():
        return 0, 0
    matches = [
        match
        for path in dataset_dir.iterdir()
        if path.is_file()
        and (match := _REPORT_FILENAME.fullmatch(path.name)) is not None
    ]
    return len(matches), len({match.group(2) for match in matches})


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
    cleaned = "".join(
        character if character.isprintable() else " "
        for character in basename
    )
    cleaned = " ".join(cleaned.split())
    return (cleaned or "Unnamed dataset")[:_MAX_ORIGINAL_FILENAME_CHARACTERS]


def _workspace_name(original_filename: str, dataset_id: str) -> str:
    candidate = Path(original_filename).stem.strip()
    return _clean_workspace_name(candidate or f"Dataset {dataset_id[:8]}")


def _clean_workspace_name(value: object) -> str:
    if not isinstance(value, str):
        raise WorkspaceHistoryError("Workspace name is required.")
    cleaned = " ".join(
        "".join(
            character if character.isprintable() else " "
            for character in value
        ).split()
    )
    if not cleaned:
        raise WorkspaceHistoryError("Workspace name is required.")
    if len(cleaned) > _MAX_WORKSPACE_NAME_CHARACTERS:
        raise WorkspaceHistoryError(
            f"Workspace name must be at most "
            f"{_MAX_WORKSPACE_NAME_CHARACTERS} characters."
        )
    return cleaned


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


def _validate_dataset_id(dataset_id: str) -> None:
    if _DATASET_ID.fullmatch(dataset_id) is None:
        raise WorkspaceHistoryError("Workspace dataset ID is invalid.")
