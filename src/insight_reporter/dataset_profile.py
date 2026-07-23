"""Deterministic format-independent profiling with no model dependency."""

import math
import re
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from insight_reporter.dataset_view import (
    CsvDatasetView,
    DatasetView,
    DatasetViewError,
)

_MISSING_MARKERS = frozenset({"", "na", "n/a", "null", "none", "nan"})
_BOOLEAN_VALUES = frozenset({"true", "false", "yes", "no", "y", "n"})
_IDENTIFIER_NAME_TOKENS = frozenset({"id", "identifier", "uuid", "guid", "key"})
_IDENTIFIER_NAME_SUFFIXES = frozenset({"code", "number", "no"})


class DatasetProfileError(ValueError):
    """Raised when a retained dataset cannot be profiled safely."""


class ColumnType(StrEnum):
    """Supported semantic column classifications."""

    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    DATETIME = "date/time"
    BOOLEAN = "boolean"
    IDENTIFIER = "identifier"
    FREE_TEXT = "free text"
    EMPTY = "empty"


@dataclass(frozen=True)
class NumericStatistics:
    count: int
    minimum: float
    maximum: float
    mean: float
    median: float
    total: float
    standard_deviation: float


@dataclass(frozen=True)
class DateRange:
    earliest: str
    latest: str


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    inferred_type: ColumnType
    missing_count: int
    missing_percentage: float
    unique_count: int
    is_constant: bool
    is_empty: bool
    numeric_statistics: NumericStatistics | None = None
    date_range: DateRange | None = None


@dataclass(frozen=True)
class DatasetProfile:
    row_count: int
    column_count: int
    columns: tuple[ColumnProfile, ...]
    source_sha256: str
    size_bytes: int
    preview_rows: tuple[tuple[str, ...], ...]
    kpi_candidates: tuple[str, ...]
    date_candidates: tuple[str, ...]
    category_candidates: tuple[str, ...]
    source_format: str = "csv"
    source_internal_filename: str = ""
    source_table_name: str | None = None

    def column(self, name: str) -> ColumnProfile | None:
        """Return one named column profile without raising on user input."""

        return next((column for column in self.columns if column.name == name), None)


def profile_csv(path: Path, *, preview_rows: int = 5) -> DatasetProfile:
    """Backward-compatible profiling entry point for a validated CSV."""

    try:
        view = CsvDatasetView.from_path(path)
        size_bytes = path.stat().st_size
    except (DatasetViewError, OSError) as error:
        raise DatasetProfileError(str(error)) from error
    return profile_dataset(view, size_bytes=size_bytes, preview_rows=preview_rows)


def profile_dataset(
    view: DatasetView,
    *,
    size_bytes: int,
    preview_rows: int = 5,
) -> DatasetProfile:
    """Profile a validated DatasetView using one deterministic type system."""

    if len(view.sources) != 1:
        raise DatasetProfileError(
            "Profiling multiple joined sources requires an explicit relationship plan."
        )
    source = view.sources[0]
    headers = view.headers
    dataset_rows = view.iter_rows()
    rows = [
        tuple(row.values.get(header, "") for header in headers)
        for row in dataset_rows
    ]
    if not rows:
        raise DatasetProfileError("Retained dataset has no data rows.")

    row_count = len(rows)
    columns: list[ColumnProfile] = []
    for index, header in enumerate(headers):
        values = [row[index] for row in rows]
        columns.append(_profile_column(header, values, row_count=row_count))

    column_profiles = tuple(columns)
    kpi_candidates = tuple(
        column.name
        for column in column_profiles
        if column.inferred_type is ColumnType.NUMERIC and not column.is_constant
    )
    date_candidates = tuple(
        column.name
        for column in column_profiles
        if column.inferred_type is ColumnType.DATETIME and not column.is_constant
    )
    category_candidates = tuple(
        column.name
        for column in column_profiles
        if not column.is_constant
        and (
            column.inferred_type is ColumnType.BOOLEAN
            or (
                column.inferred_type is ColumnType.CATEGORICAL
                and column.unique_count <= min(50, max(2, row_count // 2))
            )
        )
    )

    return DatasetProfile(
        row_count=row_count,
        column_count=len(headers),
        columns=column_profiles,
        source_sha256=source.sha256,
        size_bytes=size_bytes,
        preview_rows=tuple(rows[:preview_rows]),
        kpi_candidates=kpi_candidates,
        date_candidates=date_candidates,
        category_candidates=category_candidates,
        source_format=source.format,
        source_internal_filename=source.internal_filename,
        source_table_name=source.table_name,
    )


def _profile_column(name: str, values: list[str], *, row_count: int) -> ColumnProfile:
    present_values = [value.strip() for value in values if not _is_missing(value)]
    missing_count = row_count - len(present_values)
    unique_count = len(set(present_values))
    is_empty = not present_values
    is_constant = unique_count == 1
    inferred_type = _infer_type(name, present_values)

    numeric_statistics = None
    if inferred_type is ColumnType.NUMERIC:
        numbers = [float(value) for value in present_values]
        numeric_statistics = NumericStatistics(
            count=len(numbers),
            minimum=min(numbers),
            maximum=max(numbers),
            mean=math.fsum(numbers) / len(numbers),
            median=float(statistics.median(numbers)),
            total=math.fsum(numbers),
            standard_deviation=float(statistics.pstdev(numbers)),
        )

    date_range = None
    if inferred_type is ColumnType.DATETIME:
        parsed_dates = [_parse_datetime(value) for value in present_values]
        dates = [value for value in parsed_dates if value is not None]
        earliest = min(dates, key=_datetime_sort_key)
        latest = max(dates, key=_datetime_sort_key)
        date_range = DateRange(
            earliest=earliest.isoformat(),
            latest=latest.isoformat(),
        )

    return ColumnProfile(
        name=name,
        inferred_type=inferred_type,
        missing_count=missing_count,
        missing_percentage=(missing_count / row_count) * 100,
        unique_count=unique_count,
        is_constant=is_constant,
        is_empty=is_empty,
        numeric_statistics=numeric_statistics,
        date_range=date_range,
    )


def _infer_type(name: str, values: list[str]) -> ColumnType:
    if not values:
        return ColumnType.EMPTY

    if _identifier_name(name):
        return ColumnType.IDENTIFIER

    normalized = [value.casefold() for value in values]
    if all(value in _BOOLEAN_VALUES for value in normalized):
        return ColumnType.BOOLEAN

    parsed_dates = [_parse_datetime(value) for value in values]
    if all(value is not None for value in parsed_dates):
        return ColumnType.DATETIME

    if all(_is_number(value) for value in values):
        return ColumnType.NUMERIC

    if _value_pattern_looks_like_identifier(values):
        return ColumnType.IDENTIFIER

    if _looks_like_free_text(values):
        return ColumnType.FREE_TEXT

    return ColumnType.CATEGORICAL


def _is_missing(value: str) -> bool:
    return value.strip().casefold() in _MISSING_MARKERS


def _identifier_name(name: str) -> bool:
    tokens = [token for token in re.split(r"[^a-z0-9]+", name.casefold()) if token]
    return bool(
        set(tokens) & _IDENTIFIER_NAME_TOKENS
        or (tokens and tokens[-1] in _IDENTIFIER_NAME_SUFFIXES)
    )


def _parse_datetime(value: str) -> datetime | None:
    candidate = value.strip()
    # Compact digits such as 20260101 are commonly identifiers. Requiring a
    # date separator prevents accidental identifier-to-date conversion.
    if not any(separator in candidate for separator in ("-", "/", ":", "T", " ")):
        return None

    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(candidate, "%Y/%m/%d")
        except ValueError:
            return None


def _is_number(value: str) -> bool:
    try:
        number = float(value)
    except ValueError:
        return False
    return math.isfinite(number)


def _datetime_sort_key(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _value_pattern_looks_like_identifier(values: list[str]) -> bool:
    if len(values) < 3 or len(set(values)) != len(values):
        return False

    return all(
        len(value) <= 64
        and not any(character.isspace() for character in value)
        and bool(re.fullmatch(r"[A-Za-z0-9_.:-]+", value))
        and any(character.isdigit() for character in value)
        for value in values
    )


def _looks_like_free_text(values: list[str]) -> bool:
    lengths = [len(value) for value in values]
    average_length = math.fsum(lengths) / len(lengths)
    return (
        max(lengths) >= 100
        or average_length >= 40
        or any(len(value.split()) >= 8 for value in values)
    )
