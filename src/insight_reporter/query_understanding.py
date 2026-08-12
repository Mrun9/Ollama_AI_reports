"""Question-to-analysis routing for chat-with-data."""

from __future__ import annotations

import re
from dataclasses import dataclass

from insight_reporter.dataset_profile import ColumnType, DatasetProfile

_WORD = re.compile(r"[a-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")


@dataclass(frozen=True)
class QueryAnalysisRequest:
    question: str
    intent: str
    metric_columns: tuple[str, ...]
    dimension_columns: tuple[str, ...]
    time_columns: tuple[str, ...]
    target_columns: tuple[str, ...]
    direction: str | None
    analysis_target: str | None = None
    analysis_factor: str | None = None


def understand_question(question: str, profile: DatasetProfile) -> QueryAnalysisRequest:
    """Build a validated, schema-bound analysis request from a user question."""

    bounded = " ".join(str(question or "").split())[:1_000]
    lowered = bounded.casefold()
    mentioned = _mentioned_columns(lowered, profile)
    numeric = tuple(
        column.name
        for column in profile.columns
        if column.inferred_type is ColumnType.NUMERIC and not column.is_constant
    )
    dimensions = tuple(
        sorted(
            profile.category_candidates,
            key=lambda name: (
                profile.column(name).inferred_type is ColumnType.BOOLEAN
                if profile.column(name)
                else True,
                name,
            ),
        )
    )
    dates = tuple(profile.date_candidates)
    booleans = tuple(
        column.name
        for column in profile.columns
        if column.inferred_type is ColumnType.BOOLEAN and not column.is_constant
    )

    mentioned_numeric = tuple(column for column in mentioned if column in numeric)
    mentioned_dates = tuple(column for column in mentioned if column in dates)
    mentioned_booleans = tuple(column for column in mentioned if column in booleans)

    intent = _intent(lowered)
    direction = _direction(lowered)
    target_columns: tuple[str, ...] = ()
    if intent in {"boolean_rate", "relationship"}:
        target_columns = mentioned_booleans or booleans[:1]

    mentioned_dimensions = tuple(
        column
        for column in mentioned
        if column in dimensions and column not in target_columns
    )
    metric_columns = mentioned_numeric or (() if target_columns else numeric[:1])
    dimension_columns = mentioned_dimensions or _default_dimensions(
        dimensions,
        exclude=target_columns + metric_columns,
    )
    time_columns = mentioned_dates or dates[:1]
    if intent == "boolean_rate" and not target_columns and booleans:
        target_columns = booleans[:1]
    if intent == "trend" and not time_columns:
        intent = "summary"
    if intent == "missingness":
        metric_columns = ()
        dimension_columns = ()
    if intent == "summary":
        metric_columns = metric_columns[:3]
        dimension_columns = dimension_columns[:3]
    return QueryAnalysisRequest(
        question=bounded,
        intent=intent,
        metric_columns=metric_columns[:3],
        dimension_columns=dimension_columns[:4],
        time_columns=time_columns[:1],
        target_columns=target_columns[:1],
        direction=direction,
    )


def _intent(question: str) -> str:
    tokens = normalized_tokens(question)
    if tokens & {"missing", "null", "blank", "quality"}:
        return "missingness"
    if tokens & {"outlier", "anomaly", "anomalie", "unusual"} or "anomal" in question:
        return "outliers"
    if (
        tokens & {"trend", "recent", "change", "changed"}
        or "over time" in question
        or "by month" in question
    ):
        return "trend"
    if tokens & {
        "affect",
        "affects",
        "associated",
        "association",
        "correlate",
        "correlates",
        "differ",
        "differs",
        "factor",
        "influence",
        "influences",
        "relationship",
    }:
        return "relationship"
    if tokens & {"rate", "percentage", "proportion"}:
        return "boolean_rate"
    if tokens & {"top", "highest", "best", "bottom", "lowest", "worst"}:
        return "top_bottom"
    if tokens & {"compare", "versus", "across"} or " vs " in question or " by " in question:
        return "compare_groups"
    if tokens & {"distribution", "average", "median", "mean"}:
        return "distribution"
    return "summary"


def _direction(question: str) -> str | None:
    tokens = normalized_tokens(question)
    if tokens & {"bottom", "lowest", "worst", "least", "low"}:
        return "lowest"
    if tokens & {"top", "highest", "best", "most", "high"}:
        return "highest"
    return None


def _mentioned_columns(question: str, profile: DatasetProfile) -> tuple[str, ...]:
    question_tokens = normalized_tokens(question)
    scored: list[tuple[int, str]] = []
    for column in profile.columns:
        column_tokens = normalized_tokens(column.name)
        if not column_tokens:
            continue
        overlap = len(question_tokens & column_tokens)
        normalized_name = " ".join(_CAMEL_BOUNDARY.sub(" ", column.name).casefold().split())
        if (
            len(column_tokens) > 1
            and normalized_name in question
            or len(column_tokens) == 1
            and next(iter(column_tokens)) in question_tokens
        ):
            overlap += 3
        if overlap:
            scored.append((overlap, column.name))
    return tuple(name for _score, name in sorted(scored, key=lambda item: (-item[0], item[1])))


def normalized_tokens(value: str) -> set[str]:
    """Tokenize user/schema text consistently across deterministic and model paths."""

    expanded = _CAMEL_BOUNDARY.sub(" ", value).casefold()
    tokens = set(_WORD.findall(expanded))
    tokens.update(token[:-1] for token in tuple(tokens) if token.endswith("s") and len(token) > 3)
    return tokens


def normalized_identifier(value: str) -> str:
    """Return an ordered snake-case-like identifier for glossary matching."""

    expanded = _CAMEL_BOUNDARY.sub(" ", value).casefold()
    return "_".join(_WORD.findall(expanded))


def _default_dimensions(
    dimensions: tuple[str, ...],
    *,
    exclude: tuple[str, ...],
) -> tuple[str, ...]:
    excluded = set(exclude)
    return tuple(column for column in dimensions if column not in excluded)


def generate_suggested_questions(
    profile: DatasetProfile,
    *,
    max_questions: int = 6,
) -> tuple[str, ...]:
    """Generate deterministic first-load questions from the live profile."""

    numeric = [
        column.name
        for column in profile.columns
        if column.inferred_type is ColumnType.NUMERIC and not column.is_constant
    ]
    categorical = [
        column
        for column in profile.category_candidates
        if (profile.column(column) and profile.column(column).unique_count <= 15)
    ]
    dates = list(profile.date_candidates)
    suggestions: list[str] = []
    if numeric and categorical:
        measure, dimension = numeric[0], categorical[0]
        suggestions.extend(
            (
                f"What is the average {measure} by {dimension}?",
                f"Does {dimension} affect {measure}?",
            )
        )
    if numeric and dates:
        suggestions.append(f"How has {numeric[0]} changed over time?")
    if numeric:
        suggestions.append(f"What are the outliers in {numeric[0]}?")
    if categorical:
        suggestions.append(f"How many records are there per {categorical[0]}?")
    suggestions.append("What am I looking at?")
    return tuple(dict.fromkeys(suggestions))[: max(1, max_questions)]


def followup_questions(plan: dict[str, object]) -> tuple[str, ...]:
    """Generate deterministic follow-ups scoped to the columns in one result."""

    intent = str(plan.get("intent") or "query")
    dimensions = plan.get("dimensions") or plan.get("group_by")
    group_by = [str(item) for item in dimensions] if isinstance(dimensions, list) else []
    out: list[str] = []
    if intent == "analysis":
        target = plan.get("target")
        if isinstance(target, str):
            out.append(f"What does the distribution of {target} look like overall?")
    elif group_by:
        out.append(f"Which {group_by[0]} had the biggest change over time?")
    if plan.get("time") is None and plan.get("time_bucket") is None:
        out.append("Has this changed over time?")
    return tuple(dict.fromkeys(out))[:3]
