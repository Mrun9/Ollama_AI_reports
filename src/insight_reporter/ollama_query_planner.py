"""Ollama-backed constrained query planning for data chat."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from ollama import Client

from insight_reporter.dataset_profile import ColumnType, DatasetProfile
from insight_reporter.dataset_view import DatasetView
from insight_reporter.query_understanding import normalized_identifier, normalized_tokens


class OllamaQueryPlannerError(ValueError):
    """Raised when Ollama cannot produce a valid query plan."""


class _ChatClient(Protocol):
    def chat(self, **kwargs: Any) -> object: ...


@dataclass(frozen=True)
class QueryPlannerResult:
    plan: dict[str, Any]
    model: str
    prompt_version: str
    intent: str = "query"


_PROMPT_VERSION = "analysis_plan.v2"
_MAX_SAMPLE_VALUES = 5
_MAX_DISTINCT_VALUES = 15
_GLOSSARY_ALIASES = {
    "revenue": ("amount", "gross_sales", "revenue", "sales", "total_price"),
    "sales": ("amount", "gross_sales", "revenue", "sales", "total_price"),
    "total": ("amount", "gross_sales", "revenue", "sales", "total_price"),
    "customers": ("account_id", "client_id", "customer", "customer_id"),
    "clients": ("account_id", "client_id", "customer", "customer_id"),
    "profit": ("margin", "net_profit", "profit"),
    "cost": ("cost", "expense", "total_cost"),
}
_INTENT_PROMPT = """Classify this question into exactly one intent:
- query: needs specific data computed from the dataset
- analysis: asks whether variables affect, correlate with, or differ by one another
- overview: asks what the dataset is or asks to explain a prior result
- clarify: too ambiguous to proceed safely
- chitchat: not about the data
Return only a JSON object with the intent."""
_INTENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["query", "analysis", "overview", "clarify", "chitchat"],
        }
    },
    "required": ["intent"],
}
_ANALYSIS_PROMPT = """You are selecting variables for a statistical analysis.
Use only the supplied table and columns. The target is the outcome being compared
or measured; the factor is the variable it may differ by or correlate with.
Do not choose a statistical test and do not answer the question.
Return only the required JSON object."""
_ANALYSIS_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {"const": "analysis"},
        "table": {"const": "uploaded_data"},
        "target": {"type": "string"},
        "factor": {"type": "string"},
    },
    "required": ["intent", "table", "target", "factor"],
}
_ANALYSIS_TYPES = [
    "filtered_aggregate",
    "grouped_comparison",
    "ranking",
    "time_series",
    "distribution",
    "relationship",
    "data_quality",
    "distinct_values",
    "categorization",
    "filtered_grouped_aggregate",
]
_QUERY_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["ready", "needs_clarification"]},
        "message": {"type": "string"},
        "intent": {"type": "string", "enum": _ANALYSIS_TYPES},
        "analysis_type": {"type": "string", "enum": _ANALYSIS_TYPES},
        "measure": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "column": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        "aggregation": {
                            "type": "string",
                            "enum": ["avg", "sum", "count", "min", "max", "median"],
                        },
                    },
                    "required": ["column", "aggregation"],
                },
            ]
        },
        "metric": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "aggregation": {"type": "string"},
        "dimensions": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "group_by": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "filters": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "column": {"type": "string"},
                    "operator": {
                        "type": "string",
                        "enum": ["equals", "in", "contains", "quarter", "month", "year"],
                    },
                    "value": {},
                },
                "required": ["column", "operator", "value"],
            },
        },
        "time": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "column": {"type": "string"},
                        "grain": {
                            "type": "string",
                            "enum": ["day", "week", "month", "quarter", "year"],
                        },
                        "operation": {"type": "string"},
                    },
                    "required": ["column", "grain", "operation"],
                },
            ]
        },
        "comparisons": {"type": "object"},
        "buckets": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "operator": {
                        "type": "string",
                        "enum": ["lt", "lte", "gt", "gte", "between"],
                    },
                    "value": {"type": "number"},
                    "upper": {"type": "number"},
                },
                "required": ["label", "operator", "value"],
            },
        },
        "bucket_column": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        "assumptions": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
    "required": ["status"],
}
_SYSTEM_PROMPT = """You are a data analysis planner.
Convert the user question into a JSON analysis plan.
Return only valid JSON. Do not answer, calculate, or write SQL.

Rules:
- Use only the supplied table and columns.
- Do not invent columns.
- Use business_glossary when the user's business term differs from a column name.
- Prefer the supplied sample_values when matching categorical values.
- Supported aggregations: avg, sum, count, min, max, median.
- Supported filter operators: equals, contains, quarter, month, year.
- Choose exactly one analysis_type: filtered_aggregate, grouped_comparison, ranking,
  time_series, distribution, relationship, data_quality, distinct_values, categorization.
- Use filters only for constraints like "in Singapore", "for Electronics",
  or "where status is Closed".
- Use dimensions for phrases like "by country", "across months", "per supplier",
  or "for each category".
- Use time_series for "trend", "over time", "across months", "across quarters", or "across years".
- Do not turn a time-period sample value into a filter unless the question asks
  for one specific period.
- Use sum for additive business measures such as sales, revenue, units, quantity,
  cost, profit, amount, received, sold, or gross sales unless the user explicitly
  asks for average/mean.
- Use avg only when the user says average or mean.
- Return status="needs_clarification" only if no safe best-effort plan can be made.
- If a likely column or value match exists, return status="ready" and put
  uncertainty in assumptions.
- Use assumptions for interpretations such as "electronics means Category = Electronics"
  or "countries means Country".
- Use distinct_values for "list values", "different names", "unique values",
  "what categories", or "what countries are in column X".
- Use categorization when the user asks to classify, bucket, segment, group into
  high/medium/low, or categorize rows based on conditions.
- Use ranking for "highest", "lowest", "top", "bottom", or "most/least".
- For "highest product in each country", use analysis_type ranking, dimensions
  ["Country", "Product"], and country filters when countries are named.
- For distinct_values, put the target column in dimensions, leave measure null,
  and do not add filters unless explicitly requested.
- For categorization, include buckets with labels and numeric conditions when possible.

Required ready schema:
{
  "status": "ready",
  "analysis_type": "time_series",
  "measure": {"column": "Sales", "aggregation": "sum"},
  "dimensions": ["Month_Name"],
  "filters": [{"column": "Country", "operator": "equals", "value": "Singapore"}],
  "time": {"column": "Month_Name", "grain": "month", "operation": "trend"},
  "comparisons": {"sort": "chronological"},
  "buckets": [],
  "limit": 100,
  "assumptions": ["Interpreted Singapore as Country = Singapore"]
}"""
_REPAIR_PROMPT = """You returned a plan that was not ready. Try once more.
Choose the most likely safe plan using only supplied columns and sample_values.
Return needs_clarification only if a safe plan is impossible. Put uncertainty in assumptions.
Return only JSON."""


def plan_query_with_ollama(
    question: str,
    *,
    view: DatasetView,
    profile: DatasetProfile,
    model: str,
    host: str,
    timeout_seconds: int,
    client: _ChatClient | None = None,
) -> QueryPlannerResult:
    """Classify the question, then ask Ollama for a constrained query plan."""

    if client is None:
        client = Client(host=host, timeout=float(timeout_seconds))
    classification_messages = [
        {"role": "system", "content": _INTENT_PROMPT},
        {"role": "user", "content": question},
    ]
    payload = {
        "table": "uploaded_data",
        "columns": _column_context(view, profile, question=question),
        "business_glossary": _business_glossary(profile),
        "question": question,
    }
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        classification = _chat_json_plan(
            client,
            model=model,
            messages=classification_messages,
            format_schema=_INTENT_SCHEMA,
            num_predict=30,
        )
        intent = classification.get("intent")
        if intent not in {"query", "analysis", "overview", "clarify", "chitchat"}:
            raise OllamaQueryPlannerError("Ollama returned an invalid intent classification.")
        if intent not in {"query", "analysis"}:
            return QueryPlannerResult(
                plan={"status": "routed", "intent": intent},
                model=model,
                prompt_version=_PROMPT_VERSION,
                intent=intent,
            )
        if intent == "analysis":
            analysis_plan = _chat_json_plan(
                client,
                model=model,
                messages=[
                    {"role": "system", "content": _ANALYSIS_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                format_schema=_ANALYSIS_PLAN_SCHEMA,
                num_predict=100,
            )
            return QueryPlannerResult(
                plan={"status": "ready", **analysis_plan},
                model=model,
                prompt_version=_PROMPT_VERSION,
                intent=intent,
            )
        plan = _chat_json_plan(
            client,
            model=model,
            messages=messages,
            format_schema=_QUERY_PLAN_SCHEMA,
        )
        if isinstance(plan, dict) and plan.get("status") != "ready":
            repair_messages = [
                *messages,
                {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)},
                {"role": "user", "content": _REPAIR_PROMPT},
            ]
            plan = _chat_json_plan(
                client,
                model=model,
                messages=repair_messages,
                format_schema=_QUERY_PLAN_SCHEMA,
            )
    except OllamaQueryPlannerError:
        raise
    except json.JSONDecodeError as error:
        raise OllamaQueryPlannerError(
            f"Ollama returned invalid JSON for the query plan: {error.msg}."
        ) from error
    except Exception as error:
        raise OllamaQueryPlannerError(
            f"Ollama query planning is unavailable: {type(error).__name__}: {error}"
        ) from error
    if not isinstance(plan, dict):
        raise OllamaQueryPlannerError("Ollama returned an invalid query plan.")
    return QueryPlannerResult(
        plan=plan,
        model=model,
        prompt_version=_PROMPT_VERSION,
        intent=intent,
    )


def _chat_json_plan(
    client: _ChatClient,
    *,
    model: str,
    messages: list[dict[str, str]],
    format_schema: dict[str, object],
    num_predict: int = 700,
) -> dict[str, Any]:
    response = client.chat(
        model=model,
        messages=messages,
        format=format_schema,
        options={"temperature": 0, "num_ctx": 4096, "num_predict": num_predict},
    )
    content = _response_content(response)
    plan = json.loads(content)
    if not isinstance(plan, dict):
        raise OllamaQueryPlannerError("Ollama returned an invalid query-plan shape.")
    return plan


def _column_context(
    view: DatasetView,
    profile: DatasetProfile,
    *,
    question: str,
) -> list[dict[str, object]]:
    rows = view.iter_rows()
    context: list[dict[str, object]] = []
    for column in profile.columns:
        item: dict[str, object] = {
            "name": column.name,
            "type": column.inferred_type.value,
            "role": _role(column.inferred_type),
            "missing_count": column.missing_count,
            "unique_count": column.unique_count,
        }
        if column.inferred_type in {
            ColumnType.CATEGORICAL,
            ColumnType.BOOLEAN,
            ColumnType.IDENTIFIER,
        }:
            values = [
                row.values.get(column.name, "").strip()
                for row in rows
                if row.values.get(column.name, "").strip()
            ]
            if column.unique_count <= _MAX_DISTINCT_VALUES:
                item["distinct_values"] = sorted(
                    set(values),
                    key=lambda value: (value.casefold(), value),
                )
            else:
                item["sample_values"] = _sample_values_for_question(values, question)
        if column.date_range is not None:
            item["date_range"] = {
                "earliest": column.date_range.earliest,
                "latest": column.date_range.latest,
            }
        context.append(item)
    return context


def _business_glossary(profile: DatasetProfile) -> dict[str, list[str]]:
    """Map a small maintained set of business terms to live schema columns."""

    normalized_columns = {
        column.name: normalized_identifier(column.name)
        for column in profile.columns
    }
    glossary: dict[str, list[str]] = {}
    for term, aliases in _GLOSSARY_ALIASES.items():
        matches = [
            column
            for column, normalized in normalized_columns.items()
            if normalized in aliases
        ]
        if matches:
            glossary[term] = matches
    return glossary


def _sample_values_for_question(values: list[str], question: str) -> list[str]:
    question_tokens = normalized_tokens(question)
    matching: list[str] = []
    common = [value for value, _count in Counter(values).most_common(50)]
    for value in common:
        if normalized_tokens(value) & question_tokens:
            matching.append(value)
    ordered = [*matching, *common]
    return list(dict.fromkeys(ordered))[:_MAX_SAMPLE_VALUES]


def _role(column_type: ColumnType) -> str:
    if column_type is ColumnType.NUMERIC:
        return "measure"
    if column_type is ColumnType.DATETIME:
        return "date"
    if column_type is ColumnType.BOOLEAN:
        return "outcome_or_flag"
    if column_type is ColumnType.IDENTIFIER:
        return "identifier"
    if column_type is ColumnType.FREE_TEXT:
        return "text"
    return "dimension"


def _response_content(response: object) -> str:
    if isinstance(response, dict):
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
    else:
        message = getattr(response, "message", None)
        content = (
            getattr(message, "content", None)
            if message is not None
            else None
        )
        if content is None and isinstance(message, dict):
            content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise OllamaQueryPlannerError(
            f"Ollama returned no usable query-plan content ({type(response).__name__})."
        )
    return content
