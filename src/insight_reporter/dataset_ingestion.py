"""Secure, bounded ingestion for CSV, flat JSON, and XLSX datasets."""

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from werkzeug.datastructures import FileStorage

from insight_reporter.dataset_view import (
    DatasetViewError,
    detect_dataset_format,
    discover_xlsx_tables,
    load_dataset_view,
)

_READ_CHUNK_BYTES = 64 * 1024
_DATASET_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_SUPPORTED_EXTENSIONS = ("csv", "json", "xlsx")


class DatasetValidationError(ValueError):
    """A safe validation message and corresponding HTTP response status."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DatasetUploadResult:
    """Validated metadata for one retained source file."""

    internal_filename: str
    source_format: str
    sha256: str
    size_bytes: int
    row_count: int | None
    column_count: int | None
    table_names: tuple[str, ...] = ()

    @property
    def requires_table_selection(self) -> bool:
        return self.source_format == "xlsx" and len(self.table_names) > 1


def ingest_dataset(
    uploaded_file: FileStorage,
    *,
    upload_dir: Path,
    max_bytes: int,
    max_rows: int,
    max_columns: int,
) -> DatasetUploadResult:
    """Detect, validate, and retain one source without trusting client metadata."""

    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_token = secrets.token_hex(16)
    temporary_path = upload_dir / f".{upload_token}.{secrets.token_hex(8)}.part"
    inspection_path: Path | None = None
    final_path: Path | None = None
    digest = hashlib.sha256()
    size_bytes = 0

    try:
        with temporary_path.open("xb") as destination:
            while chunk := uploaded_file.stream.read(_READ_CHUNK_BYTES):
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise DatasetValidationError(
                        f"Dataset exceeds the maximum size of {max_bytes} bytes.",
                        status_code=413,
                    )
                digest.update(chunk)
                destination.write(chunk)
        if size_bytes == 0:
            raise DatasetValidationError("Dataset is empty.")
        try:
            source_format = detect_dataset_format(temporary_path.read_bytes())
        except DatasetViewError as error:
            raise DatasetValidationError(str(error)) from error

        inspection_path = upload_dir / (
            f".{upload_token}.{secrets.token_hex(8)}.{source_format}"
        )
        temporary_path.replace(inspection_path)
        final_path = upload_dir / f"{upload_token}.{source_format}"

        if source_format == "xlsx":
            try:
                table_names = discover_xlsx_tables(inspection_path)
                view = (
                    load_dataset_view(
                        inspection_path,
                        table_name=table_names[0],
                        max_rows=max_rows,
                        max_columns=max_columns,
                    )
                    if len(table_names) == 1
                    else None
                )
            except DatasetViewError as error:
                raise DatasetValidationError(str(error)) from error
        else:
            table_names = ()
            try:
                view = load_dataset_view(
                    inspection_path,
                    max_rows=max_rows,
                    max_columns=max_columns,
                )
            except DatasetViewError as error:
                raise DatasetValidationError(str(error)) from error

        inspection_path.replace(final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        if inspection_path is not None:
            inspection_path.unlink(missing_ok=True)
        if final_path is not None:
            final_path.unlink(missing_ok=True)
        raise

    return DatasetUploadResult(
        internal_filename=final_path.name,
        source_format=source_format,
        sha256=digest.hexdigest(),
        size_bytes=size_bytes,
        row_count=view.sources[0].row_count if view is not None else None,
        column_count=view.sources[0].column_count if view is not None else None,
        table_names=table_names,
    )


def find_dataset_path(upload_dir: Path, dataset_id: str) -> Path | None:
    """Resolve one server-generated dataset ID to an allowed retained file."""

    if _DATASET_ID_PATTERN.fullmatch(dataset_id) is None:
        return None
    matches = [
        upload_dir / f"{dataset_id}.{extension}"
        for extension in _SUPPORTED_EXTENSIONS
        if (upload_dir / f"{dataset_id}.{extension}").is_file()
    ]
    if len(matches) > 1:
        raise DatasetValidationError("Retained dataset identity is ambiguous.")
    return matches[0] if matches else None


def save_xlsx_selection(
    upload_dir: Path, dataset_id: str, table_name: str
) -> Path:
    """Atomically retain a validated worksheet selection outside the static tree."""

    if _DATASET_ID_PATTERN.fullmatch(dataset_id) is None:
        raise DatasetValidationError("Dataset ID is invalid.")
    if not table_name or len(table_name) > 31 or not table_name.isprintable():
        raise DatasetValidationError("Excel worksheet selection is invalid.")
    final_path = _selection_path(upload_dir, dataset_id)
    temporary_path = upload_dir / (
        f".{dataset_id}.{secrets.token_hex(8)}.selection.part"
    )
    try:
        temporary_path.write_text(
            json.dumps({"table_name": table_name}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary_path.replace(final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return final_path


def load_xlsx_selection(upload_dir: Path, dataset_id: str) -> str | None:
    """Load a worksheet selection from an exact server-controlled path."""

    if _DATASET_ID_PATTERN.fullmatch(dataset_id) is None:
        return None
    path = _selection_path(upload_dir, dataset_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatasetValidationError("Saved Excel worksheet selection is unreadable.") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"table_name"}
        or not isinstance(payload.get("table_name"), str)
    ):
        raise DatasetValidationError("Saved Excel worksheet selection is invalid.")
    return payload["table_name"]


def _selection_path(upload_dir: Path, dataset_id: str) -> Path:
    return upload_dir / f"{dataset_id}.selection.json"
