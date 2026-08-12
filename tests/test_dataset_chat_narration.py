import json
from pathlib import Path

import pytest

from insight_reporter.dataset_chat import answer_dataset_question
from insight_reporter.dataset_chat_narration import (
    DatasetChatNarrationError,
    narrate_verified_answer,
)
from insight_reporter.dataset_profile import profile_dataset
from insight_reporter.dataset_view import load_dataset_view
from insight_reporter.ollama_query_planner import QueryPlannerResult
from insight_reporter.query_insight_engine import QueryInsight


class _NarrationClient:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[dict[str, object]] = []

    def chat(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return {"message": {"content": json.dumps({"answer": self.answer})}}


def _insight() -> QueryInsight:
    return QueryInsight(
        insight_type="trend",
        title="Unit cost trend",
        finding="Average Unit_Cost_USD increased from 62.40 to 78.10.",
        columns_used=("RecordedAt", "Unit_Cost_USD"),
        calculation="period_average",
        supporting_data=(
            {"period": "January", "value": 62.40, "records": 140},
            {"period": "March", "value": 78.10, "records": 148},
        ),
        relevance_score=1.0,
    )


def test_narration_rewrites_verified_evidence_with_low_temperature() -> None:
    client = _NarrationClient(
        "Unit cost increased over time, rising from 62.40 in January to 78.10 in March."
    )

    answer = narrate_verified_answer(
        question="How has Unit_Cost_USD changed over time?",
        verified_answer="Average Unit_Cost_USD increased from 62.40 to 78.10.",
        insights=(_insight(),),
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
        client=client,
    )

    assert "increased over time" in answer
    assert client.calls[0]["options"]["temperature"] == 0.1
    payload = json.loads(client.calls[0]["messages"][1]["content"])
    assert payload["verified_answer"].startswith("Average Unit_Cost_USD")
    assert payload["evidence"][0]["supporting_data"][0]["value"] == 62.4


def test_narration_rejects_numbers_absent_from_verified_evidence() -> None:
    client = _NarrationClient("Unit cost increased by 99 percent.")

    with pytest.raises(DatasetChatNarrationError, match="introduced numbers"):
        narrate_verified_answer(
            question="How has Unit_Cost_USD changed over time?",
            verified_answer="Average Unit_Cost_USD increased from 62.40 to 78.10.",
            insights=(_insight(),),
            model="test-model",
            host="http://127.0.0.1:11434",
            timeout_seconds=1,
            client=client,
        )


def test_chat_keeps_verified_answer_when_narration_introduces_a_number(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "sales.csv"
    path.write_text("Region,Sales\nNorth,10\nSouth,20\n", encoding="utf-8")
    view = load_dataset_view(path)
    profile = profile_dataset(view, size_bytes=path.stat().st_size)
    monkeypatch.setattr(
        "insight_reporter.dataset_chat.plan_query_with_ollama",
        lambda *_args, **_kwargs: QueryPlannerResult(
            plan={
                "status": "ready",
                "analysis_type": "filtered_aggregate",
                "measure": {"column": "Sales", "aggregation": "sum"},
                "dimensions": [],
                "filters": [],
            },
            model="test-model",
            prompt_version="test",
            intent="query",
        ),
    )
    monkeypatch.setattr(
        "insight_reporter.dataset_chat.narrate_verified_answer",
        lambda **_kwargs: (_ for _ in ()).throw(
            DatasetChatNarrationError("introduced an unverified number")
        ),
    )

    turn = answer_dataset_question(
        "What is total sales?",
        view=view,
        profile=profile,
        use_model_planner=True,
        use_model_narration=True,
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
    )

    assert turn.answer == "The total Sales is 30, based on 2 matching records."
    assert turn.verified_answer == turn.answer
    assert turn.narration_status == "fallback"
    assert turn.narration_error == "introduced an unverified number"
