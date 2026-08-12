"""Validated query-plan compilation for DuckDB-backed chat answers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rapidfuzz import process, utils

from insight_reporter.dataset_profile import ColumnType, DatasetProfile
from insight_reporter.query_data_store import QueryDataStore, quote_identifier
from insight_reporter.query_insight_engine import QueryInsight


class QueryPlanError(ValueError):
    """Raised when a model-proposed query plan is unsafe or unsupported."""


class QueryPlanClarification(QueryPlanError):
    """Raised when a model plan references data that is not in the live schema."""


_AGGREGATIONS = {
    "avg": "AVG",
    "mean": "AVG",
    "sum": "SUM",
    "total": "SUM",
    "count": "COUNT",
    "min": "MIN",
    "minimum": "MIN",
    "max": "MAX",
    "maximum": "MAX",
    "median": "MEDIAN",
}
_FILTER_OPERATORS = {"equals", "in", "contains", "quarter", "month", "year"}


@dataclass(frozen=True)
class CompiledQueryPlan:
    sql: str
    params: tuple[object, ...]
    calculation: str
    columns_used: tuple[str, ...]
    filters: tuple[dict[str, object], ...]
    aggregation: str
    metric: str | None
    group_by: tuple[str, ...]
    assumptions: tuple[str, ...]
    analysis_type: str
    bucket_column: str | None = None
    buckets: tuple[dict[str, object], ...] = ()
    time_column: str | None = None
    time_grain: str | None = None


def compile_query_plan(
    plan: dict[str, Any],
    *,
    profile: DatasetProfile,
    table_name: str,
    store: QueryDataStore | None = None,
) -> CompiledQueryPlan:
    """Validate a constrained JSON query plan and compile it to parameterized SQL."""

    if plan.get("status") != "ready":
        message = plan.get("message")
        status = plan.get("status")
        raise QueryPlanError(
            f"{status}: {message}"
            if message
            else "The model could not produce a ready query plan."
        )
    columns = {column.name: column for column in profile.columns}
    analysis_type = _analysis_type(plan)
    measure = plan.get("measure") if isinstance(plan.get("measure"), dict) else {}
    raw_aggregation = (
        measure.get("aggregation")
        if isinstance(measure, dict) and measure.get("aggregation")
        else plan.get("aggregation") or _default_aggregation(plan)
    )
    aggregation_key = _text(raw_aggregation).casefold()
    aggregation = _AGGREGATIONS.get(aggregation_key)
    if aggregation is None:
        raise QueryPlanError("The query plan requested an unsupported aggregation.")

    metric = _optional_column(
        (
            measure.get("column")
            if isinstance(measure, dict) and measure.get("column")
            else plan.get("metric")
        ),
        columns,
    )
    if aggregation != "COUNT":
        if metric is None:
            raise QueryPlanError("The query plan needs a numeric metric.")
        metric_profile = columns[metric]
        if metric_profile.inferred_type is not ColumnType.NUMERIC:
            raise QueryPlanError("The query plan metric must be numeric.")

    dimensions = plan.get("dimensions")
    group_by_source = dimensions if isinstance(dimensions, list) else plan.get("group_by")
    group_by = tuple(
        column
        for raw_column in _list(group_by_source)
        if (column := _optional_column(raw_column, columns)) is not None
    )[:3]
    time = plan.get("time") if isinstance(plan.get("time"), dict) else {}
    time_column = _optional_column(
        time.get("column") if isinstance(time, dict) else None,
        columns,
    )
    time_grain = _text(time.get("grain") if isinstance(time, dict) else "").casefold() or None
    if analysis_type == "time_series":
        if time_column is None and group_by:
            time_column = group_by[0]
        if time_column is None:
            raise QueryPlanError("A time-series plan needs a time column or period dimension.")
        group_by = (time_column,)
    if analysis_type == "distinct_values" and not group_by:
        raise QueryPlanError("A distinct-values plan needs one dimension column.")
    filters = _filters(plan.get("filters"), columns)
    if store is not None:
        filters = _ground_string_filters(filters, columns=columns, store=store)
    group_by = tuple(
        column
        for column in group_by
        if not _fixed_by_filter(column, filters)
    )
    if analysis_type in {"grouped_comparison", "ranking"} and not group_by:
        analysis_type = "filtered_aggregate"
    assumptions = tuple(
        str(item).strip()
        for item in _list(plan.get("assumptions"))
        if str(item).strip()
    )[:5]
    where_sql, params = _where_clause(filters, columns)
    buckets = _buckets(plan.get("buckets"))
    bucket_column = _optional_column(plan.get("bucket_column"), columns) or metric
    if analysis_type == "distinct_values":
        sql = _distinct_values_sql(
            table_name=table_name,
            column=group_by[0],
            where_sql=where_sql,
        )
    elif analysis_type == "categorization" and bucket_column and buckets:
        sql = _categorization_sql(
            table_name=table_name,
            column=bucket_column,
            buckets=buckets,
            where_sql=where_sql,
        )
    elif analysis_type == "ranking" and len(group_by) >= 2:
        sql = _ranking_sql(
            table_name=table_name,
            group_by=group_by,
            aggregation=aggregation,
            metric=metric,
            where_sql=where_sql,
        )
    else:
        metric_sql = "*" if aggregation == "COUNT" else quote_identifier(metric or "")
        select_parts = [f"{aggregation}({metric_sql}) AS value", "COUNT(*) AS records"]
        group_sql = ""
        order_sql = ""
        if group_by:
            group_select = [
                f"{quote_identifier(column)} AS {quote_identifier(column)}"
                for column in group_by
            ]
            select_parts = [*group_select, *select_parts]
            group_sql = " GROUP BY " + ", ".join(quote_identifier(column) for column in group_by)
            order_sql = (
                f" ORDER BY {_time_order_expression(group_by[0], columns)} ASC NULLS LAST, "
                f"{quote_identifier(group_by[0])} ASC"
                if analysis_type == "time_series"
                else " ORDER BY value DESC"
            )
        sql = (
            f"SELECT {', '.join(select_parts)} "
            f"FROM {quote_identifier(table_name)}"
            f"{where_sql}{group_sql}{order_sql}"
        )
    columns_used = tuple(
        dict.fromkeys(
            (
                *group_by,
                *(item["column"] for item in filters),
                *( [bucket_column] if bucket_column else [] ),
                *( [metric] if metric else [] ),
            )
        )
    )
    return CompiledQueryPlan(
        sql=sql,
        params=tuple(params),
        calculation="validated_model_query_plan",
        columns_used=columns_used,
        filters=filters,
        aggregation=aggregation,
        metric=metric,
        group_by=group_by,
        assumptions=assumptions,
        analysis_type=analysis_type,
        bucket_column=bucket_column,
        buckets=buckets,
        time_column=time_column,
        time_grain=time_grain,
    )


def execute_compiled_plan(
    compiled: CompiledQueryPlan,
    *,
    store: QueryDataStore,
    question: str,
) -> QueryInsight:
    """Execute one compiled query and package it as deterministic chat evidence."""

    result = store.query(compiled.sql, compiled.params, limit=100)
    if not result.rows:
        raise QueryPlanError("The validated query returned no rows.")
    row = result.rows[0]
    value = row.get("value")
    records = row.get("records")
    filter_text = _filter_text(compiled.filters)
    metric_text = compiled.metric or "records"
    aggregation_text = _aggregation_label(compiled.aggregation)
    if compiled.analysis_type == "time_series":
        finding = _time_series_finding(
            result.rows,
            metric=metric_text,
            aggregation=aggregation_text,
            time_column=compiled.group_by[0] if compiled.group_by else compiled.time_column,
        )
    elif compiled.analysis_type == "distinct_values":
        finding = _distinct_values_finding(result.rows, column=compiled.group_by[0])
    elif compiled.analysis_type == "categorization":
        finding = _categorization_finding(
            result.rows,
            column=compiled.bucket_column or compiled.metric or "selected value",
        )
    elif compiled.analysis_type == "ranking" and len(compiled.group_by) >= 2:
        finding = _ranking_finding(
            result.rows,
            parent_column=compiled.group_by[0],
            item_column=compiled.group_by[1],
            metric=metric_text,
            aggregation=aggregation_text,
        )
    elif compiled.group_by:
        finding = _grouped_finding(
            result.rows,
            group_by=compiled.group_by,
            metric=metric_text,
            aggregation=aggregation_text,
            filter_text=filter_text,
        )
    else:
        finding = (
            f"The {aggregation_text} {metric_text} is {_format_value(value)}"
            f"{f' for {filter_text}' if filter_text else ''}, based on {records} matching records."
        )
    limitations = [
        "The query plan was proposed by Ollama but validated and executed by Python/DuckDB.",
    ]
    limitations.extend(f"Assumption: {assumption}" for assumption in compiled.assumptions)
    return QueryInsight(
        insight_type="validated_model_query",
        title=(
            "Validated time-series analysis"
            if compiled.analysis_type == "time_series"
            else "Validated distinct values"
            if compiled.analysis_type == "distinct_values"
            else "Validated categorization"
            if compiled.analysis_type == "categorization"
            else "Validated DuckDB query"
        ),
        finding=finding,
        columns_used=compiled.columns_used,
        calculation=compiled.calculation,
        supporting_data=result.rows,
        relevance_score=0.98,
        limitations=tuple(limitations),
    )


def _filters(raw_filters: object, columns: dict[str, object]) -> tuple[dict[str, object], ...]:
    filters: list[dict[str, object]] = []
    for raw_filter in _list(raw_filters)[:8]:
        if not isinstance(raw_filter, dict):
            continue
        column = _optional_column(raw_filter.get("column"), columns)
        operator = _text(raw_filter.get("operator")).casefold()
        if column is None or operator not in _FILTER_OPERATORS:
            continue
        value = raw_filter.get("value")
        if value is None or isinstance(value, dict):
            continue
        if operator != "in" and isinstance(value, list):
            continue
        filters.append({"column": column, "operator": operator, "value": value})
    return tuple(filters)


def _analysis_type(plan: dict[str, Any]) -> str:
    candidate = _text(plan.get("analysis_type") or plan.get("intent")).casefold()
    allowed = {
        "filtered_aggregate",
        "grouped_comparison",
        "ranking",
        "time_series",
        "distribution",
        "relationship",
        "data_quality",
        "distinct_values",
        "categorization",
        # Legacy names.
        "filtered_grouped_aggregate",
    }
    if candidate not in allowed:
        return "filtered_aggregate"
    if candidate == "filtered_grouped_aggregate":
        return "grouped_comparison"
    return candidate


def _ground_string_filters(
    filters: tuple[dict[str, object], ...],
    *,
    columns: dict[str, object],
    store: QueryDataStore,
) -> tuple[dict[str, object], ...]:
    """Resolve categorical equality values against live distinct values."""

    grounded: list[dict[str, object]] = []
    for item in filters:
        column = str(item["column"])
        operator = str(item["operator"])
        column_profile = columns[column]
        value = item["value"]
        if (
            column_profile.inferred_type is not ColumnType.NUMERIC
            and operator in {"equals", "in"}
        ):
            if operator == "in":
                value = [
                    _resolve_value(store, column, raw_value)
                    for raw_value in _list(value)
                ]
            else:
                value = _resolve_value(store, column, value)
        grounded.append({**item, "value": value})
    return tuple(grounded)


def _resolve_value(
    store: QueryDataStore,
    column: str,
    raw_value: object,
    *,
    threshold: float = 80,
) -> object:
    distinct_values = store.distinct_values(column)
    if not distinct_values:
        raise QueryPlanClarification(f"The column {column} has no values to match.")
    raw_text = str(raw_value)
    for value in distinct_values:
        if str(value).casefold() == raw_text.casefold():
            return value
    display_values = [str(value) for value in distinct_values]
    matched = process.extractOne(
        raw_text,
        display_values,
        processor=utils.default_process,
    )
    if matched is not None:
        match_text, score, index = matched
        if score >= threshold:
            return distinct_values[index]
        raise QueryPlanClarification(
            f"I couldn't find a confident match for '{raw_text}' in {column}; "
            f"the closest value is '{match_text}'."
        )
    raise QueryPlanClarification(
        f"I couldn't find a confident match for '{raw_text}' in {column}."
    )


def _default_aggregation(plan: dict[str, Any]) -> str:
    analysis_type = _analysis_type(plan)
    if analysis_type == "time_series":
        return "sum"
    if analysis_type in {"distinct_values", "categorization"}:
        return "count"
    return "count"


def _buckets(raw_buckets: object) -> tuple[dict[str, object], ...]:
    buckets: list[dict[str, object]] = []
    for raw_bucket in _list(raw_buckets)[:10]:
        if not isinstance(raw_bucket, dict):
            continue
        label = _text(raw_bucket.get("label"))
        operator = _text(raw_bucket.get("operator")).casefold()
        value = raw_bucket.get("value")
        upper = raw_bucket.get("upper")
        if (
            label
            and operator in {"lt", "lte", "gt", "gte", "between"}
            and value is not None
        ):
            bucket = {"label": label, "operator": operator, "value": value}
            if operator == "between" and upper is not None:
                bucket["upper"] = upper
            buckets.append(bucket)
    return tuple(buckets)


def _distinct_values_sql(*, table_name: str, column: str, where_sql: str) -> str:
    column_sql = quote_identifier(column)
    return (
        f"SELECT {column_sql} AS value, COUNT(*) AS records "
        f"FROM {quote_identifier(table_name)}"
        f"{where_sql}"
        f"{' AND' if where_sql else ' WHERE'} {column_sql} IS NOT NULL "
        f"GROUP BY {column_sql} "
        f"ORDER BY {column_sql} ASC"
    )


def _categorization_sql(
    *,
    table_name: str,
    column: str,
    buckets: tuple[dict[str, object], ...],
    where_sql: str,
) -> str:
    column_sql = quote_identifier(column)
    case_parts: list[str] = []
    for bucket in buckets:
        label = str(bucket["label"]).replace("'", "''")
        operator = str(bucket["operator"])
        value = _bucket_number(bucket["value"])
        if operator == "lt":
            condition = f"{column_sql} < {value}"
        elif operator == "lte":
            condition = f"{column_sql} <= {value}"
        elif operator == "gt":
            condition = f"{column_sql} > {value}"
        elif operator == "gte":
            condition = f"{column_sql} >= {value}"
        else:
            upper = bucket.get("upper")
            if upper is None:
                continue
            condition = (
                f"{column_sql} >= {value} AND "
                f"{column_sql} <= {_bucket_number(upper)}"
            )
        case_parts.append(f"WHEN {condition} THEN '{label}'")
    if not case_parts:
        raise QueryPlanError("Categorization needs at least one valid bucket.")
    case_sql = "CASE " + " ".join(case_parts) + " ELSE 'Uncategorized' END"
    return (
        f"SELECT {case_sql} AS category, COUNT(*) AS records, "
        f"AVG({column_sql}) AS average_value "
        f"FROM {quote_identifier(table_name)}"
        f"{where_sql}"
        f" GROUP BY category ORDER BY records DESC"
    )


def _ranking_sql(
    *,
    table_name: str,
    group_by: tuple[str, ...],
    aggregation: str,
    metric: str | None,
    where_sql: str,
) -> str:
    parent = quote_identifier(group_by[0])
    item = quote_identifier(group_by[1])
    metric_sql = "*" if aggregation == "COUNT" else quote_identifier(metric or "")
    return (
        "WITH ranked_values AS ("
        f"SELECT {parent} AS parent_value, {item} AS item_value, "
        f"{aggregation}({metric_sql}) AS value, COUNT(*) AS records, "
        f"ROW_NUMBER() OVER (PARTITION BY {parent} ORDER BY "
        f"{aggregation}({metric_sql}) DESC) AS rank "
        f"FROM {quote_identifier(table_name)}"
        f"{where_sql} "
        f"GROUP BY {parent}, {item}"
        ") "
        "SELECT parent_value, item_value, value, records "
        "FROM ranked_values WHERE rank = 1 ORDER BY parent_value ASC"
    )


def _where_clause(
    filters: tuple[dict[str, object], ...],
    columns: dict[str, object],
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    for item in filters:
        column = str(item["column"])
        operator = str(item["operator"])
        value = item["value"]
        column_profile = columns[column]
        column_sql = quote_identifier(column)
        if operator == "equals":
            if column_profile.inferred_type is ColumnType.NUMERIC:
                clauses.append(f"{column_sql} = ?")
                params.append(value)
            else:
                clauses.append(f"LOWER(CAST({column_sql} AS VARCHAR)) = LOWER(?)")
                params.append(str(value))
        elif operator == "in":
            values = [item for item in _list(value) if not isinstance(item, (dict, list))]
            if not values:
                continue
            if column_profile.inferred_type is ColumnType.NUMERIC:
                clauses.append(
                    f"{column_sql} IN ({', '.join('?' for _item in values)})"
                )
                params.extend(values)
            else:
                clauses.append(
                    f"LOWER(CAST({column_sql} AS VARCHAR)) IN "
                    f"({', '.join('LOWER(?)' for _item in values)})"
                )
                params.extend(str(item) for item in values)
        elif operator == "contains":
            clauses.append(f"LOWER(CAST({column_sql} AS VARCHAR)) LIKE LOWER(?)")
            params.append(f"%{value}%")
        elif operator in {"quarter", "month", "year"}:
            if column_profile.inferred_type is not ColumnType.DATETIME:
                raise QueryPlanError("Date period filters must use a date column.")
            clauses.append(f"EXTRACT('{operator}' FROM {column_sql}) = ?")
            params.append(int(value))
    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def _time_order_expression(column: str, columns: dict[str, object]) -> str:
    column_profile = columns[column]
    column_sql = quote_identifier(column)
    if column_profile.inferred_type is ColumnType.DATETIME:
        return column_sql
    cast_sql = f"CAST({column_sql} AS VARCHAR)"
    return (
        "COALESCE("
        f"TRY_STRPTIME({cast_sql}, '%b-%Y'), "
        f"TRY_STRPTIME({cast_sql}, '%B-%Y'), "
        f"TRY_STRPTIME({cast_sql}, '%Y-%m'), "
        f"TRY_STRPTIME({cast_sql}, '%m-%Y')"
        ")"
    )


def _optional_column(value: object, columns: dict[str, object]) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in columns:
        raise QueryPlanClarification(f"Unknown column: {value}")
    return value


def _fixed_by_filter(column: str, filters: tuple[dict[str, object], ...]) -> bool:
    return any(
        item.get("column") == column and item.get("operator") == "equals"
        for item in filters
    )


def _bucket_number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise QueryPlanError("Categorization buckets require numeric thresholds.") from error


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _filter_text(filters: tuple[dict[str, object], ...]) -> str:
    if not filters:
        return ""
    parts = []
    for item in filters:
        operator = str(item["operator"])
        value = item["value"]
        if operator == "quarter":
            value = f"Q{value}"
        parts.append(f"{item['column']} {operator.replace('_', ' ')} {value}")
    return ", ".join(parts)


def _time_series_finding(
    rows: tuple[dict[str, object], ...],
    *,
    metric: str,
    aggregation: str,
    time_column: str | None,
) -> str:
    if not rows:
        return f"No time-series values were returned for {metric}."
    period_column = time_column or "period"
    first = rows[0]
    last = rows[-1]
    values = [row for row in rows if row.get("value") is not None]
    if not values:
        return f"The time-series query returned periods but no usable {metric} values."
    peak = max(values, key=lambda row: _number(row.get("value")))
    trough = min(values, key=lambda row: _number(row.get("value")))
    first_value = _number(first.get("value"))
    last_value = _number(last.get("value"))
    change = last_value - first_value
    direction = "increased" if change > 0 else "decreased" if change < 0 else "was flat"
    return (
        f"The {aggregation} {metric} across {period_column} {direction} from "
        f"{_format_value(first.get('value'))} in {first.get(period_column)} to "
        f"{_format_value(last.get('value'))} in {last.get(period_column)}. "
        f"The highest period is {peak.get(period_column)} at {_format_value(peak.get('value'))}, "
        f"and the lowest period is {trough.get(period_column)} at "
        f"{_format_value(trough.get('value'))}."
    )


def _distinct_values_finding(
    rows: tuple[dict[str, object], ...],
    *,
    column: str,
) -> str:
    values = [str(row.get("value")) for row in rows if row.get("value") is not None]
    preview = ", ".join(values[:12])
    suffix = f" Values: {preview}." if preview else ""
    return f"{column} has {len(values)} distinct value(s).{suffix}"


def _categorization_finding(
    rows: tuple[dict[str, object], ...],
    *,
    column: str,
) -> str:
    if not rows:
        return f"No rows could be categorized by {column}."
    largest = rows[0]
    return (
        f"Rows were categorized by {column} into {len(rows)} bucket(s). "
        f"The largest bucket is {largest.get('category')} with "
        f"{largest.get('records')} records."
    )


def _ranking_finding(
    rows: tuple[dict[str, object], ...],
    *,
    parent_column: str,
    item_column: str,
    metric: str,
    aggregation: str,
) -> str:
    if not rows:
        return f"No ranking results were returned for {metric}."
    parts = [
        (
            f"{row.get('parent_value')}: {row.get('item_value')} "
            f"({_format_value(row.get('value'))})"
        )
        for row in rows[:8]
    ]
    return (
        f"The top {item_column} by {aggregation} {metric} for each "
        f"{parent_column} is: {', '.join(parts)}."
    )


def _grouped_finding(
    rows: tuple[dict[str, object], ...],
    *,
    group_by: tuple[str, ...],
    metric: str,
    aggregation: str,
    filter_text: str,
) -> str:
    if not rows:
        return f"No grouped values were returned for {metric}."
    if len(rows) == 1:
        row = rows[0]
        group_text = ", ".join(
            f"{column} {row.get(column)}"
            for column in group_by
            if column in row
        )
        return (
            f"The {aggregation} {metric} is {_format_value(row.get('value'))}"
            f"{f' for {filter_text}' if filter_text else ''}"
            f"{f' ({group_text})' if group_text else ''}, based on "
            f"{row.get('records')} matching records."
        )
    group_column = group_by[-1]
    top = rows[0]
    low = rows[-1]
    preview = ", ".join(
        f"{row.get(group_column)} {_format_value(row.get('value'))}"
        for row in rows[:5]
    )
    return (
        f"The highest {aggregation} {metric} by {', '.join(group_by)} is "
        f"{top.get(group_column)} at {_format_value(top.get('value'))}; "
        f"the lowest is {low.get(group_column)} at {_format_value(low.get('value'))}. "
        f"Top values: {preview}."
    )


def _aggregation_label(aggregation: str) -> str:
    return {
        "AVG": "mean",
        "SUM": "total",
        "COUNT": "count of",
        "MIN": "minimum",
        "MAX": "maximum",
        "MEDIAN": "median",
    }.get(aggregation, aggregation.casefold())


def _format_value(value: object) -> str:
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    return str(value)


def _number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
