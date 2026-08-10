"""Chat-with-data orchestration built on deterministic query insights."""

from __future__ import annotations

from dataclasses import dataclass

from insight_reporter.dataset_profile import DatasetProfile
from insight_reporter.dataset_view import DatasetView
from insight_reporter.query_data_store import QueryDataStore, QueryDataStoreError
from insight_reporter.query_insight_engine import (
    QueryInsight,
    deterministic_answer,
    generate_query_insights,
)
from insight_reporter.query_understanding import (
    QueryAnalysisRequest,
    understand_question,
)

_MAX_QUESTION_CHARACTERS = 1_000


class DatasetChatError(ValueError):
    """Raised when a chat turn cannot be produced safely."""


@dataclass(frozen=True)
class DatasetChatTurn:
    question: str
    answer: str
    analysis_request: QueryAnalysisRequest
    insights: tuple[QueryInsight, ...]
    model_status: str


def answer_dataset_question(
    question: str,
    *,
    view: DatasetView,
    profile: DatasetProfile,
) -> DatasetChatTurn:
    """Answer one question using query-specific deterministic calculations."""

    bounded_question = _bounded_question(question)
    request = understand_question(bounded_question, profile)
    try:
        store = QueryDataStore.from_view(view, profile=profile)
        insights = generate_query_insights(request, profile=profile, store=store)
    except QueryDataStoreError as error:
        raise DatasetChatError(str(error)) from error
    answer = deterministic_answer(bounded_question, insights)
    return DatasetChatTurn(
        question=bounded_question,
        answer=answer,
        analysis_request=request,
        insights=insights,
        model_status="deterministic_only",
    )


def _bounded_question(question: str) -> str:
    text = " ".join(str(question or "").split())
    if not text:
        raise DatasetChatError("Enter a question about the uploaded data.")
    if len(text) > _MAX_QUESTION_CHARACTERS:
        raise DatasetChatError(
            f"Questions are limited to {_MAX_QUESTION_CHARACTERS} characters."
        )
    return text
