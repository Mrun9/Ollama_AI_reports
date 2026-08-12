"""Chat-with-data orchestration built on deterministic query insights."""

from __future__ import annotations

from dataclasses import dataclass, replace

from insight_reporter.dataset_chat_narration import (
    DatasetChatNarrationError,
    narrate_verified_answer,
)
from insight_reporter.dataset_profile import DatasetProfile
from insight_reporter.dataset_view import DatasetView
from insight_reporter.ollama_query_planner import (
    OllamaQueryPlannerError,
    plan_query_with_ollama,
)
from insight_reporter.query_data_store import QueryDataStore, QueryDataStoreError
from insight_reporter.query_insight_engine import (
    QueryInsight,
    deterministic_answer,
    generate_query_insights,
)
from insight_reporter.query_plan_compiler import (
    QueryPlanClarification,
    QueryPlanError,
    compile_query_plan,
    execute_compiled_plan,
)
from insight_reporter.query_understanding import (
    QueryAnalysisRequest,
    followup_questions,
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
    planner_error: str | None = None
    suggested_questions: tuple[str, ...] = ()
    narration_status: str = "not_requested"
    narration_error: str | None = None
    verified_answer: str = ""


def answer_dataset_question(
    question: str,
    *,
    view: DatasetView,
    profile: DatasetProfile,
    use_model_planner: bool = False,
    model: str = "",
    host: str = "",
    timeout_seconds: int = 1,
    conversation_state: dict[str, object] | None = None,
    use_model_narration: bool = False,
) -> DatasetChatTurn:
    """Answer one question using query-specific deterministic calculations."""

    bounded_question = _bounded_question(question)
    request = understand_question(bounded_question, profile)
    store: QueryDataStore | None = None
    planner_error: str | None = None
    if use_model_planner:
        try:
            planner_result = plan_query_with_ollama(
                bounded_question,
                view=view,
                profile=profile,
                model=model,
                host=host,
                timeout_seconds=timeout_seconds,
            )
            routed = _non_query_turn(
                planner_result.intent,
                question=bounded_question,
                request=request,
                profile=profile,
                conversation_state=conversation_state,
            )
            if routed is not None:
                return routed
            if planner_result.intent not in {"query", "analysis"}:
                raise OllamaQueryPlannerError(
                    "Ollama's non-query intent conflicted with the schema-bound "
                    f"{request.intent} request; using deterministic analysis."
                )
            store = QueryDataStore.from_view(view, profile=profile)
            try:
                if planner_result.intent == "analysis":
                    analysis_request = _analysis_request_from_plan(
                        planner_result.plan,
                        request=request,
                        profile=profile,
                        table_name=store.table_name,
                    )
                    insights = generate_query_insights(
                        analysis_request,
                        profile=profile,
                        store=store,
                    )
                    turn = DatasetChatTurn(
                        question=bounded_question,
                        answer=deterministic_answer(bounded_question, insights),
                        analysis_request=analysis_request,
                        insights=insights,
                        model_status="analysis_routed",
                        suggested_questions=followup_questions(planner_result.plan),
                    )
                    turn = _with_narration(
                        turn,
                        enabled=use_model_narration,
                        model=model,
                        host=host,
                        timeout_seconds=timeout_seconds,
                    )
                    _remember_turn(
                        conversation_state,
                        turn,
                        sql=None,
                    )
                    return turn
                compiled = compile_query_plan(
                    planner_result.plan,
                    profile=profile,
                    table_name=store.table_name,
                    store=store,
                )
                insight = execute_compiled_plan(
                    compiled,
                    store=store,
                    question=bounded_question,
                )
                turn = DatasetChatTurn(
                    question=bounded_question,
                    answer=deterministic_answer(bounded_question, (insight,)),
                    analysis_request=request,
                    insights=(insight,),
                    model_status="ollama_plan_validated",
                    suggested_questions=followup_questions(
                        {**planner_result.plan, "intent": "query"}
                    ),
                )
                turn = _with_narration(
                    turn,
                    enabled=use_model_narration,
                    model=model,
                    host=host,
                    timeout_seconds=timeout_seconds,
                )
                _remember_turn(conversation_state, turn, sql=compiled.sql)
                return turn
            except QueryPlanClarification as error:
                return _clarification_turn(bounded_question, request, str(error))
            except QueryPlanError as error:
                planner_error = str(error)
        except OllamaQueryPlannerError as error:
            planner_error = str(error)
    try:
        if store is None:
            store = QueryDataStore.from_view(view, profile=profile)
        insights = generate_query_insights(request, profile=profile, store=store)
    except QueryDataStoreError as error:
        raise DatasetChatError(str(error)) from error
    answer = deterministic_answer(bounded_question, insights)
    turn = DatasetChatTurn(
        question=bounded_question,
        answer=answer,
        analysis_request=request,
        insights=insights,
        model_status=(
            "fallback_after_planner_error"
            if use_model_planner and planner_error
            else "deterministic_only"
        ),
        planner_error=planner_error,
    )
    turn = _with_narration(
        turn,
        enabled=use_model_narration,
        model=model,
        host=host,
        timeout_seconds=timeout_seconds,
    )
    _remember_turn(conversation_state, turn, sql=None)
    return turn


def _with_narration(
    turn: DatasetChatTurn,
    *,
    enabled: bool,
    model: str,
    host: str,
    timeout_seconds: int,
) -> DatasetChatTurn:
    turn = replace(
        turn,
        verified_answer=turn.verified_answer or turn.answer,
    )
    if not enabled or not turn.insights:
        return turn
    try:
        answer = narrate_verified_answer(
            question=turn.question,
            verified_answer=turn.answer,
            insights=turn.insights,
            model=model,
            host=host,
            timeout_seconds=timeout_seconds,
        )
    except DatasetChatNarrationError as error:
        return replace(
            turn,
            narration_status="fallback",
            narration_error=str(error),
        )
    return replace(turn, answer=answer, narration_status="generated")


def _non_query_turn(
    intent: str,
    *,
    question: str,
    request: QueryAnalysisRequest,
    profile: DatasetProfile,
    conversation_state: dict[str, object] | None,
) -> DatasetChatTurn | None:
    if intent == "chitchat":
        return DatasetChatTurn(
            question=question,
            answer="Hi! I can help you explore and analyze the uploaded dataset.",
            analysis_request=request,
            insights=(),
            model_status="chitchat",
        )
    if intent == "clarify":
        return _clarification_turn(
            question,
            request,
            "Please specify the columns, measure, or comparison you mean.",
        )
    if intent == "overview":
        # Only summary-like questions may reuse the previous result. A model can
        # occasionally label concrete requests such as "changed over time" as an
        # overview; those must fall through to a fresh deterministic calculation.
        if request.intent != "summary":
            return None
        if conversation_state and conversation_state.get("last_result") is not None:
            return DatasetChatTurn(
                question=question,
                answer=_last_result_overview(conversation_state),
                analysis_request=request,
                insights=(),
                model_status="overview",
            )
        columns = ", ".join(column.name for column in profile.columns[:8])
        return DatasetChatTurn(
            question=question,
            answer=(
                f"The dataset contains {profile.row_count} rows and "
                f"{profile.column_count} columns. Available columns include: {columns}."
            ),
            analysis_request=request,
            insights=(),
            model_status="overview",
        )
    return None


def _clarification_turn(
    question: str,
    request: QueryAnalysisRequest,
    reason: str,
) -> DatasetChatTurn:
    return DatasetChatTurn(
        question=question,
        answer=f"I need clarification before querying the data. {reason}",
        analysis_request=request,
        insights=(),
        model_status="needs_clarification",
    )


def _analysis_request_from_plan(
    plan: dict[str, object],
    *,
    request: QueryAnalysisRequest,
    profile: DatasetProfile,
    table_name: str,
) -> QueryAnalysisRequest:
    table = plan.get("table")
    if table != table_name:
        raise QueryPlanClarification(f"Unknown table: {table}")
    target = plan.get("target")
    factor = plan.get("factor")
    columns = {column.name: column for column in profile.columns}
    for column in (target, factor):
        if not isinstance(column, str) or column not in columns:
            raise QueryPlanClarification(f"Unknown column: {column}")
    if target == factor:
        raise QueryPlanClarification("Analysis target and factor must be different columns.")
    return replace(
        request,
        intent="relationship",
        metric_columns=tuple(
            column
            for column in (target, factor)
            if columns[column].inferred_type.value == "numeric"
        ),
        dimension_columns=tuple(
            column
            for column in (factor, target)
            if columns[column].inferred_type.value != "numeric"
        ),
        analysis_target=target,
        analysis_factor=factor,
    )


def _remember_turn(
    conversation_state: dict[str, object] | None,
    turn: DatasetChatTurn,
    *,
    sql: str | None,
) -> None:
    if conversation_state is None or not turn.insights:
        return
    result_rows = [
        dict(row)
        for insight in turn.insights[:4]
        for row in insight.supporting_data[:12]
    ][:20]
    conversation_state.update(
        last_question=turn.question,
        last_sql=sql,
        last_result=result_rows,
    )


def _last_result_overview(conversation_state: dict[str, object]) -> str:
    last_question = str(conversation_state.get("last_question") or "the previous question")
    raw_rows = conversation_state.get("last_result")
    rows = raw_rows if isinstance(raw_rows, list) else []
    preview = "; ".join(
        ", ".join(f"{key}={value}" for key, value in row.items())
        for row in rows[:3]
        if isinstance(row, dict)
    )
    return (
        f"Your previous question was “{last_question}”. "
        f"The result showed {preview or 'no result rows'}; this is the specific result "
        "currently in context."
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
