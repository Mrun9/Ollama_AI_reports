"""Question-specific deterministic insight generation."""

from __future__ import annotations

import math
from dataclasses import dataclass

from insight_reporter.dataset_profile import ColumnType, DatasetProfile
from insight_reporter.query_data_store import QueryDataStore, quote_identifier
from insight_reporter.query_understanding import QueryAnalysisRequest


@dataclass(frozen=True)
class QueryInsight:
    insight_type: str
    title: str
    finding: str
    columns_used: tuple[str, ...]
    calculation: str
    supporting_data: tuple[dict[str, object], ...]
    relevance_score: float
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "insight_type": self.insight_type,
            "title": self.title,
            "finding": self.finding,
            "columns_used": list(self.columns_used),
            "calculation": self.calculation,
            "supporting_data": list(self.supporting_data),
            "relevance_score": self.relevance_score,
            "limitations": list(self.limitations),
        }


def generate_query_insights(
    request: QueryAnalysisRequest,
    *,
    profile: DatasetProfile,
    store: QueryDataStore,
) -> tuple[QueryInsight, ...]:
    """Generate a focused set of Python/DuckDB-calculated facts."""

    insights: list[QueryInsight] = []
    if request.intent == "missingness":
        insights.extend(_missingness(profile))
    elif request.intent == "top_bottom":
        insights.extend(_top_bottom(request, store=store))
    elif request.intent == "compare_groups":
        insights.extend(_compare_groups(request, store=store))
    elif request.intent == "trend":
        insights.extend(_trend(request, store=store))
    elif request.intent == "outliers":
        insights.extend(_outliers(request, profile=profile, store=store))
    elif request.intent == "distribution":
        insights.extend(_distribution(request, profile=profile))
    elif request.intent == "boolean_rate":
        insights.extend(_boolean_rate(request, store=store))
    elif request.intent == "relationship":
        insights.extend(_relationship(request, profile=profile, store=store))
    else:
        insights.extend(_summary(request, profile=profile, store=store))
    if not insights:
        insights.extend(_summary(request, profile=profile, store=store))
    return tuple(
        sorted(insights, key=lambda insight: insight.relevance_score, reverse=True)[:8]
    )


def deterministic_answer(question: str, insights: tuple[QueryInsight, ...]) -> str:
    """Create a readable answer from computed facts without model wording."""

    if not insights:
        return "I could not find enough structured evidence in the uploaded data to answer that."
    lead = insights[0].finding
    supporting = [insight.finding for insight in insights[1:4]]
    if not supporting:
        return lead
    return " ".join((lead, *supporting))


def _summary(
    request: QueryAnalysisRequest,
    *,
    profile: DatasetProfile,
    store: QueryDataStore,
) -> list[QueryInsight]:
    result = store.query(f"SELECT COUNT(*) AS row_count FROM {quote_identifier(store.table_name)}")
    row_count = result.rows[0]["row_count"] if result.rows else profile.row_count
    findings = [
        QueryInsight(
            insight_type="dataset_summary",
            title="Dataset size",
            finding=(
                f"The dataset contains {row_count} rows and {profile.column_count} columns."
            ),
            columns_used=(),
            calculation="count_rows_and_columns",
            supporting_data=result.rows,
            relevance_score=0.72,
        )
    ]
    findings.extend(_missingness(profile)[:1])
    for column in request.metric_columns[:2]:
        column_profile = profile.column(column)
        if column_profile and column_profile.numeric_statistics:
            stats = column_profile.numeric_statistics
            findings.append(
                QueryInsight(
                    insight_type="numeric_summary",
                    title=f"{column} summary",
                    finding=(
                        f"{column} has a median of {_fmt(stats.median)} and ranges from "
                        f"{_fmt(stats.minimum)} to {_fmt(stats.maximum)}."
                    ),
                    columns_used=(column,),
                    calculation="profile_numeric_statistics",
                    supporting_data=(),
                    relevance_score=0.64,
                )
            )
    return findings


def _missingness(profile: DatasetProfile) -> list[QueryInsight]:
    affected = [
        column for column in profile.columns if column.missing_count > 0
    ]
    if not affected:
        return [
            QueryInsight(
                insight_type="missingness",
                title="Missing data",
                finding="No missing values were detected using the configured missing-value markers.",
                columns_used=(),
                calculation="profile_missingness",
                supporting_data=(),
                relevance_score=0.84,
            )
        ]
    top = sorted(affected, key=lambda column: column.missing_percentage, reverse=True)[:5]
    leader = top[0]
    return [
        QueryInsight(
            insight_type="missingness",
            title="Missing data",
            finding=(
                f"{len(affected)} columns contain missing values; {leader.name} has the "
                f"highest missingness at {_fmt(leader.missing_percentage)}%."
            ),
            columns_used=tuple(column.name for column in top),
            calculation="profile_missingness",
            supporting_data=tuple(
                {
                    "column": column.name,
                    "missing_count": column.missing_count,
                    "missing_percentage": round(column.missing_percentage, 3),
                }
                for column in top
            ),
            relevance_score=0.9,
        )
    ]


def _top_bottom(request: QueryAnalysisRequest, *, store: QueryDataStore) -> list[QueryInsight]:
    metric = request.metric_columns[:1]
    dimension = request.dimension_columns[:1]
    if not dimension:
        return []
    dimension_sql = quote_identifier(dimension[0])
    if metric:
        metric_sql = quote_identifier(metric[0])
        value_expr = f"AVG({metric_sql})"
        label = f"average {metric[0]}"
        columns = (dimension[0], metric[0])
    else:
        value_expr = "COUNT(*)"
        label = "record count"
        columns = (dimension[0],)
    order = "ASC" if request.direction == "lowest" else "DESC"
    result = store.query(
        f"""
        SELECT {dimension_sql} AS group_value, {value_expr} AS value, COUNT(*) AS records
        FROM {quote_identifier(store.table_name)}
        WHERE {dimension_sql} IS NOT NULL
        GROUP BY {dimension_sql}
        HAVING COUNT(*) > 0
        ORDER BY value {order}
        """,
        limit=10,
    )
    if not result.rows:
        return []
    top = result.rows[0]
    direction = "lowest" if request.direction == "lowest" else "highest"
    return [
        QueryInsight(
            insight_type="top_bottom",
            title=f"{direction.title()} {label} by {dimension[0]}",
            finding=(
                f"{top['group_value']} has the {direction} {label} at {_fmt(top['value'])} "
                f"across {top['records']} records."
            ),
            columns_used=columns,
            calculation="grouped_top_bottom",
            supporting_data=result.rows,
            relevance_score=0.95,
        )
    ]


def _compare_groups(request: QueryAnalysisRequest, *, store: QueryDataStore) -> list[QueryInsight]:
    if not request.metric_columns or not request.dimension_columns:
        return _top_bottom(request, store=store)
    metric = request.metric_columns[0]
    dimension = request.dimension_columns[0]
    result = store.query(
        f"""
        SELECT {quote_identifier(dimension)} AS group_value,
               AVG({quote_identifier(metric)}) AS average_value,
               COUNT(*) AS records
        FROM {quote_identifier(store.table_name)}
        WHERE {quote_identifier(dimension)} IS NOT NULL
          AND {quote_identifier(metric)} IS NOT NULL
        GROUP BY {quote_identifier(dimension)}
        ORDER BY average_value DESC
        """,
        limit=12,
    )
    if len(result.rows) < 2:
        return []
    high = result.rows[0]
    low = result.rows[-1]
    gap = _num(high["average_value"]) - _num(low["average_value"])
    return [
        QueryInsight(
            insight_type="group_comparison",
            title=f"{metric} by {dimension}",
            finding=(
                f"{metric} differs by {dimension}: {high['group_value']} is highest at "
                f"{_fmt(high['average_value'])}, while {low['group_value']} is lowest at "
                f"{_fmt(low['average_value'])}; the gap is {_fmt(gap)}."
            ),
            columns_used=(dimension, metric),
            calculation="average_measure_by_dimension",
            supporting_data=result.rows,
            relevance_score=0.92,
        )
    ]


def _boolean_rate(request: QueryAnalysisRequest, *, store: QueryDataStore) -> list[QueryInsight]:
    if not request.target_columns or not request.dimension_columns:
        return []
    target = request.target_columns[0]
    dimension = request.dimension_columns[0]
    result = store.query(
        f"""
        SELECT {quote_identifier(dimension)} AS group_value,
               AVG(CASE WHEN {quote_identifier(target)} THEN 1.0 ELSE 0.0 END) * 100 AS rate,
               COUNT(*) AS records
        FROM {quote_identifier(store.table_name)}
        WHERE {quote_identifier(dimension)} IS NOT NULL
          AND {quote_identifier(target)} IS NOT NULL
        GROUP BY {quote_identifier(dimension)}
        ORDER BY rate DESC
        """,
        limit=12,
    )
    if not result.rows:
        return []
    top = result.rows[0]
    return [
        QueryInsight(
            insight_type="boolean_rate",
            title=f"{target} rate by {dimension}",
            finding=(
                f"{top['group_value']} has the highest {target} rate at "
                f"{_fmt(top['rate'])}% across {top['records']} records."
            ),
            columns_used=(dimension, target),
            calculation="boolean_rate_by_dimension",
            supporting_data=result.rows,
            relevance_score=0.95,
            limitations=("Rates are descriptive and do not prove cause.",),
        )
    ]


def _relationship(
    request: QueryAnalysisRequest,
    *,
    profile: DatasetProfile,
    store: QueryDataStore,
) -> list[QueryInsight]:
    insights: list[QueryInsight] = []
    if request.target_columns:
        for dimension in request.dimension_columns[:3]:
            insights.extend(
                _boolean_rate(
                    QueryAnalysisRequest(
                        question=request.question,
                        intent="boolean_rate",
                        metric_columns=(),
                        dimension_columns=(dimension,),
                        time_columns=(),
                        target_columns=request.target_columns,
                        direction=None,
                    ),
                    store=store,
                )
            )
        target = request.target_columns[0]
        for metric in request.metric_columns[:3]:
            result = store.query(
                f"""
                SELECT {quote_identifier(target)} AS target_value,
                       AVG({quote_identifier(metric)}) AS average_value,
                       COUNT(*) AS records
                FROM {quote_identifier(store.table_name)}
                WHERE {quote_identifier(target)} IS NOT NULL
                  AND {quote_identifier(metric)} IS NOT NULL
                GROUP BY {quote_identifier(target)}
                ORDER BY target_value DESC
                """,
                limit=5,
            )
            if len(result.rows) >= 2:
                insights.append(
                    QueryInsight(
                        insight_type="target_numeric_difference",
                        title=f"{metric} by {target}",
                        finding=(
                            f"Average {metric} differs across {target} groups: "
                            + "; ".join(
                                f"{row['target_value']} is {_fmt(row['average_value'])}"
                                for row in result.rows
                            )
                            + "."
                        ),
                        columns_used=(target, metric),
                        calculation="numeric_average_by_boolean_target",
                        supporting_data=result.rows,
                        relevance_score=0.86,
                        limitations=("Differences are descriptive and do not prove cause.",),
                    )
                )
    if not insights:
        numeric = tuple(
            column.name
            for column in profile.columns
            if column.inferred_type is ColumnType.NUMERIC
        )
        if len(numeric) >= 2:
            metric_a, metric_b = numeric[:2]
            result = store.query(
                f"""
                SELECT CORR({quote_identifier(metric_a)}, {quote_identifier(metric_b)})
                  AS correlation,
                  COUNT(*) AS records
                FROM {quote_identifier(store.table_name)}
                WHERE {quote_identifier(metric_a)} IS NOT NULL
                  AND {quote_identifier(metric_b)} IS NOT NULL
                """,
                limit=1,
            )
            if result.rows and result.rows[0]["correlation"] is not None:
                insights.append(
                    QueryInsight(
                        insight_type="numeric_relationship",
                        title=f"{metric_a} and {metric_b}",
                        finding=(
                            f"{metric_a} and {metric_b} have a Pearson association of "
                            f"{_fmt(result.rows[0]['correlation'])} across "
                            f"{result.rows[0]['records']} records."
                        ),
                        columns_used=(metric_a, metric_b),
                        calculation="pearson_correlation",
                        supporting_data=result.rows,
                        relevance_score=0.72,
                        limitations=("Association does not prove causation.",),
                    )
                )
    return insights


def _trend(request: QueryAnalysisRequest, *, store: QueryDataStore) -> list[QueryInsight]:
    if not request.time_columns:
        return []
    date_column = request.time_columns[0]
    metric = request.metric_columns[:1]
    value_expr = f"AVG({quote_identifier(metric[0])})" if metric else "COUNT(*)"
    label = f"average {metric[0]}" if metric else "record count"
    columns = (date_column, *metric)
    result = store.query(
        f"""
        SELECT DATE_TRUNC('month', {quote_identifier(date_column)}) AS period,
               {value_expr} AS value,
               COUNT(*) AS records
        FROM {quote_identifier(store.table_name)}
        WHERE {quote_identifier(date_column)} IS NOT NULL
        GROUP BY period
        ORDER BY period
        """,
        limit=60,
    )
    if len(result.rows) < 2:
        return []
    first = result.rows[0]
    last = result.rows[-1]
    change = _num(last["value"]) - _num(first["value"])
    return [
        QueryInsight(
            insight_type="trend",
            title=f"{label.title()} over time",
            finding=(
                f"{label.title()} changed from {_fmt(first['value'])} in {first['period']} "
                f"to {_fmt(last['value'])} in {last['period']}, a {_signed(change)} change."
            ),
            columns_used=columns,
            calculation="monthly_trend",
            supporting_data=result.rows[-12:],
            relevance_score=0.9,
        )
    ]


def _distribution(request: QueryAnalysisRequest, *, profile: DatasetProfile) -> list[QueryInsight]:
    insights: list[QueryInsight] = []
    for metric in request.metric_columns[:3]:
        column = profile.column(metric)
        if not column or not column.numeric_statistics:
            continue
        stats = column.numeric_statistics
        insights.append(
            QueryInsight(
                insight_type="numeric_distribution",
                title=f"{metric} distribution",
                finding=(
                    f"{metric} has an average of {_fmt(stats.mean)}, a median of "
                    f"{_fmt(stats.median)}, and a standard deviation of "
                    f"{_fmt(stats.standard_deviation)}."
                ),
                columns_used=(metric,),
                calculation="profile_numeric_distribution",
                supporting_data=(
                    {
                        "minimum": round(stats.minimum, 6),
                        "maximum": round(stats.maximum, 6),
                        "mean": round(stats.mean, 6),
                        "median": round(stats.median, 6),
                        "standard_deviation": round(stats.standard_deviation, 6),
                    },
                ),
                relevance_score=0.84,
            )
        )
    return insights


def _outliers(
    request: QueryAnalysisRequest,
    *,
    profile: DatasetProfile,
    store: QueryDataStore,
) -> list[QueryInsight]:
    insights: list[QueryInsight] = []
    for metric in request.metric_columns[:2]:
        result = store.query(
            f"""
            WITH stats AS (
              SELECT
                QUANTILE_CONT({quote_identifier(metric)}, 0.25) AS q1,
                QUANTILE_CONT({quote_identifier(metric)}, 0.75) AS q3,
                MEDIAN({quote_identifier(metric)}) AS median_value
              FROM {quote_identifier(store.table_name)}
              WHERE {quote_identifier(metric)} IS NOT NULL
            ),
            bounds AS (
              SELECT q1, q3, median_value, (q3 - q1) AS iqr FROM stats
            )
            SELECT COUNT(*) AS outlier_count,
                   MIN({quote_identifier(metric)}) AS minimum_outlier,
                   MAX({quote_identifier(metric)}) AS maximum_outlier,
                   MAX(median_value) AS median_value
            FROM {quote_identifier(store.table_name)}, bounds
            WHERE {quote_identifier(metric)} < q1 - 1.5 * iqr
               OR {quote_identifier(metric)} > q3 + 1.5 * iqr
            """,
            limit=1,
        )
        if not result.rows:
            continue
        row = result.rows[0]
        insights.append(
            QueryInsight(
                insight_type="outliers",
                title=f"{metric} outliers",
                finding=(
                    f"{metric} has {row['outlier_count']} IQR outliers; outlier values range "
                    f"from {_fmt(row['minimum_outlier'])} to {_fmt(row['maximum_outlier'])}, "
                    f"compared with a median of {_fmt(row['median_value'])}."
                ),
                columns_used=(metric,),
                calculation="iqr_outlier_count",
                supporting_data=result.rows,
                relevance_score=0.86,
            )
        )
    if not insights and not request.metric_columns:
        request = QueryAnalysisRequest(
            question=request.question,
            intent=request.intent,
            metric_columns=tuple(profile.kpi_candidates[:2]),
            dimension_columns=request.dimension_columns,
            time_columns=request.time_columns,
            target_columns=request.target_columns,
            direction=request.direction,
        )
        return _outliers(request, profile=profile, store=store)
    return insights


def _num(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _fmt(value: object) -> str:
    if value is None:
        return "not available"
    number = _num(value)
    if abs(number) >= 100:
        return f"{number:,.2f}".rstrip("0").rstrip(".")
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _signed(value: float) -> str:
    return f"+{_fmt(value)}" if value > 0 else _fmt(value)
