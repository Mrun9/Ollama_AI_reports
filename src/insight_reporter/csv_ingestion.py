"""Secure, bounded ingestion for one local CSV upload."""

import csv
import hashlib
import io
import secrets
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from werkzeug.datastructures import FileStorage

_READ_CHUNK_BYTES = 64 * 1024
_ALLOWED_CONTROL_CHARACTERS = {"\t", "\n", "\r"}


class CSVValidationError(ValueError):
    """A safe validation message and corresponding HTTP response status."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class CSVUploadResult:
    """Validated metadata and a small, display-safe preview data model."""

    internal_filename: str
    sha256: str
    size_bytes: int
    headers: tuple[str, ...]
    preview_rows: tuple[tuple[str, ...], ...]
    row_count: int
    column_count: int


@dataclass(frozen=True)
class _CSVInspection:
    headers: tuple[str, ...]
    preview_rows: tuple[tuple[str, ...], ...]
    row_count: int
    column_count: int


def ingest_csv(
    uploaded_file: FileStorage,
    *,
    upload_dir: Path,
    max_bytes: int,
    max_rows: int,
    max_columns: int,
    preview_rows: int,
) -> CSVUploadResult:
    """Store and validate one upload without trusting client metadata.

    Bytes are first written under a randomized temporary name. Invalid or
    incomplete files are removed, and only a fully validated file is promoted
    to its randomized ``.csv`` name.
    """

    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_token = secrets.token_hex(16)
    temporary_path = upload_dir / f"{upload_token}.part"
    final_path = upload_dir / f"{upload_token}.csv"

    digest = hashlib.sha256()
    size_bytes = 0

    try:
        with temporary_path.open("xb") as destination:
            while chunk := uploaded_file.stream.read(_READ_CHUNK_BYTES):
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise CSVValidationError(
                        f"CSV exceeds the maximum size of {max_bytes} bytes.",
                        status_code=413,
                    )
                digest.update(chunk)
                destination.write(chunk)

        if size_bytes == 0:
            raise CSVValidationError("CSV is empty.")

        inspection = _inspect_csv(
            temporary_path,
            max_rows=max_rows,
            max_columns=max_columns,
            preview_rows=preview_rows,
        )
        temporary_path.replace(final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise

    return CSVUploadResult(
        internal_filename=final_path.name,
        sha256=digest.hexdigest(),
        size_bytes=size_bytes,
        headers=inspection.headers,
        preview_rows=inspection.preview_rows,
        row_count=inspection.row_count,
        column_count=inspection.column_count,
    )


def _inspect_csv(
    path: Path,
    *,
    max_rows: int,
    max_columns: int,
    preview_rows: int,
) -> _CSVInspection:
    raw_bytes = path.read_bytes()

    try:
        text = raw_bytes.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as error:
        raise CSVValidationError(
            "File must be UTF-8 CSV text; binary or non-UTF-8 content was rejected."
        ) from error

    if not text.strip():
        raise CSVValidationError("CSV is empty.")

    if any(
        unicodedata.category(character) == "Cc"
        and character not in _ALLOWED_CONTROL_CHARACTERS
        for character in text
    ):
        raise CSVValidationError("Binary or unsafe control characters were detected.")

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)

    try:
        raw_headers = next(reader)
    except StopIteration as error:
        raise CSVValidationError("CSV is empty.") from error
    except csv.Error as error:
        raise _malformed_csv_error(error, line_number=1) from error

    headers = tuple(header.strip() for header in raw_headers)
    if not headers or any(not header for header in headers):
        raise CSVValidationError("CSV header contains an empty column name.")

    column_count = len(headers)
    if column_count > max_columns:
        raise CSVValidationError(
            f"CSV has {column_count} columns; the maximum is {max_columns}."
        )

    normalized_headers = [unicodedata.normalize("NFKC", header).casefold() for header in headers]
    duplicate_headers = sorted(
        {header for header in normalized_headers if normalized_headers.count(header) > 1}
    )
    if duplicate_headers:
        duplicates = ", ".join(duplicate_headers)
        raise CSVValidationError(f"CSV contains duplicate column names: {duplicates}.")

    preview: list[tuple[str, ...]] = []
    row_count = 0

    try:
        for row in reader:
            source_line = reader.line_num
            # A physically blank line is emitted as an empty record. Ignore it
            # because trailing blank lines are common in otherwise valid CSV
            # exports; nonblank rows with the wrong width remain malformed.
            if not row:
                continue
            if len(row) != column_count:
                raise CSVValidationError(
                    f"Malformed CSV row near line {source_line}: expected "
                    f"{column_count} columns but found {len(row)}."
                )

            row_count += 1
            if row_count > max_rows:
                raise CSVValidationError(
                    f"CSV has more than the maximum of {max_rows} data rows."
                )

            if len(preview) < preview_rows:
                preview.append(tuple(row))
    except csv.Error as error:
        raise _malformed_csv_error(error, line_number=reader.line_num) from error

    if row_count == 0:
        raise CSVValidationError("CSV must contain at least one data row.")

    return _CSVInspection(
        headers=headers,
        preview_rows=tuple(preview),
        row_count=row_count,
        column_count=column_count,
    )


def _malformed_csv_error(error: csv.Error, *, line_number: int) -> CSVValidationError:
    return CSVValidationError(f"Malformed CSV near line {line_number}: {error}.")
