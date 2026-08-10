"""DuckDB-backed query access for uploaded tabular datasets."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from insight_reporter.dataset_profile import ColumnType, DatasetProfile
from insight_reporter.dataset_view import DatasetView


class QueryDataStoreError(ValueError):
    """Raised when query-time analysis cannot safely access a dataset."""


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[dict[str, object], ...]


class QueryDataStore:
    """Small read-only DuckDB wrapper around one retained DatasetView."""

    def __init__(self, connection: Any, *, table_name: str = "uploaded_data") -> None:
        self._connection = connection
        self.table_name = table_name

    @classmethod
    def from_view(
        cls,
        view: DatasetView,
        *,
        profile: DatasetProfile,
    ) -> "QueryDataStore":
        """Materialize the validated source rows into an in-memory DuckDB table."""

        try:
            import duckdb
        except ImportError as error:
            raise QueryDataStoreError(
                "DuckDB is not installed. Install project dependencies before using data chat."
            ) from error

        if len(view.sources) != 1:
            raise QueryDataStoreError("Data chat currently supports one uploaded source table.")
        connection = duckdb.connect(database=":memory:")
        table = "uploaded_data"
        column_sql = ", ".join(
            f"{_quote_identifier(column.name)} {_duckdb_type(column.inferred_type)}"
            for column in profile.columns
        )
        connection.execute(f"CREATE TABLE {_quote_identifier(table)} ({column_sql})")
        headers = tuple(column.name for column in profile.columns)
        placeholders = ", ".join("?" for _header in headers)
        insert_sql = f"INSERT INTO {_quote_identifier(table)} VALUES ({placeholders})"
        rows = [
            tuple(
                _coerce_value(row.values.get(header, ""), profile.column(header).inferred_type)
                for header in headers
            )
            for row in view.iter_rows()
        ]
        if rows:
            connection.executemany(insert_sql, rows)
        return cls(connection, table_name=table)

    def query(
        self,
        sql: str,
        params: tuple[object, ...] = (),
        *,
        limit: int = 100,
    ) -> QueryResult:
        """Execute one internally generated read-only query and return dict rows."""

        if not sql.lstrip().upper().startswith("SELECT "):
            raise QueryDataStoreError("Only SELECT queries are allowed.")
        safe_limit = max(1, min(int(limit), 500))
        limited_sql = f"SELECT * FROM ({sql}) AS chat_query_result LIMIT {safe_limit}"
        try:
            cursor = self._connection.execute(limited_sql, params)
            columns = tuple(item[0] for item in cursor.description)
            rows = tuple(
                {
                    column: _clean_value(value)
                    for column, value in zip(columns, row, strict=True)
                }
                for row in cursor.fetchall()
            )
        except Exception as error:
            raise QueryDataStoreError("Query-time analysis failed safely.") from error
        return QueryResult(columns=columns, rows=rows)


def quote_identifier(name: str) -> str:
    """Expose safe DuckDB identifier quoting for deterministic generators."""

    return _quote_identifier(name)


def _duckdb_type(column_type: ColumnType) -> str:
    if column_type is ColumnType.NUMERIC:
        return "DOUBLE"
    if column_type is ColumnType.DATETIME:
        return "TIMESTAMP"
    if column_type is ColumnType.BOOLEAN:
        return "BOOLEAN"
    return "VARCHAR"


def _coerce_value(value: str, column_type: ColumnType) -> object:
    text = value.strip()
    if text.casefold() in {"", "na", "n/a", "null", "none", "nan"}:
        return None
    if column_type is ColumnType.NUMERIC:
        try:
            number = float(text)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    if column_type is ColumnType.BOOLEAN:
        normalized = text.casefold()
        if normalized in {"true", "yes", "y"}:
            return True
        if normalized in {"false", "no", "n"}:
            return False
        return None
    if column_type is ColumnType.DATETIME:
        return _parse_datetime(text)
    return text


def _parse_datetime(value: str) -> datetime | None:
    candidate = value.strip()
    for suffix in ("Z", "z"):
        if candidate.endswith(suffix):
            candidate = f"{candidate[:-1]}+00:00"
            break
    for fmt in (
        None,
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ):
        try:
            return (
                datetime.fromisoformat(candidate)
                if fmt is None
                else datetime.strptime(candidate, fmt)
            )
        except ValueError:
            continue
    return None


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _clean_value(value: object) -> object:
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, 6)
    if isinstance(value, datetime):
        return value.isoformat()
    return value
