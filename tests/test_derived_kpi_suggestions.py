"""Structured derived-KPI Ollama suggestion tests."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from insight_reporter.dataset_profile import profile_csv
from insight_reporter.derived_kpi_suggestions import (
    DerivedKpiSuggestionError,
    build_derived_kpi_profile_summary,
    build_derived_kpi_response_schema,
    generate_derived_kpi_suggestions,
    parse_derived_kpi_response,
)


class FakeChatClient:
    def __init__(self, content: str = "", error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(message=SimpleNamespace(content=self.content))


def _profile(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "sales.csv"
    path.write_text(
        "date,region,revenue,cost\n"
        "2026-01-01,PRIVATE-NORTH,100,60\n"
        "2026-01-02,PRIVATE-SOUTH,200,120\n"
        "2026-01-03,PRIVATE-NORTH,300,150\n",
        encoding="utf-8",
    )
    return profile_csv(path)


def _suggestion(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "operation": "subtract",
        "left_column": "revenue",
        "right_column": "cost",
        "display_format": "currency",
        "kpi_direction": "higher",
        "date_column": "date",
        "category_columns": ["region"],
        "benchmark_strategy": "dataset_mean",
        "business_objective": "Evaluate derived profitability by region over time.",
        "confidence": 0.9,
        "rationale": ["Profit adds meaning beyond either source column alone."],
    }
    value.update(overrides)
    return value


def test_valid_derived_kpi_suggestion_is_accepted(tmp_path: Path) -> None:
    batch = parse_derived_kpi_response(
        json.dumps({"suggestions": [_suggestion()]}),
        profile=_profile(tmp_path),
    )

    assert len(batch.suggestions) == 1
    assert batch.rejected_count == 0
    assert batch.suggestions[0].metric.formula_label == "revenue - cost"
    assert batch.suggestions[0].metric.source_columns == ("revenue", "cost")
    assert batch.suggestions[0].date_column == "date"
    assert batch.suggestions[0].category_columns == ("region",)
    assert batch.suggestions[0].benchmark_strategy == "dataset_mean"
    assert batch.suggestions[0].business_objective.startswith("Evaluate")


def test_hallucinated_columns_and_executable_operations_are_rejected(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path)

    with pytest.raises(DerivedKpiSuggestionError, match="no valid"):
        parse_derived_kpi_response(
            json.dumps(
                {
                    "suggestions": [
                        _suggestion(
                            operation="__import__",
                            left_column="invented_profit",
                        )
                    ]
                }
            ),
            profile=profile,
        )


def test_model_receives_metadata_and_restricted_schema_only(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    client = FakeChatClient(json.dumps({"suggestions": [_suggestion()]}))

    generate_derived_kpi_suggestions(
        profile,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=5,
        client=client,
    )

    call = client.calls[0]
    prompt = call["messages"][1]["content"]
    schema = call["format"]
    properties = schema["properties"]["suggestions"]["items"]["properties"]
    assert "numeric_columns" in prompt
    assert "PRIVATE-NORTH" not in prompt
    assert "preview_rows" not in prompt
    assert properties["left_column"]["enum"] == ["revenue", "cost"]
    assert "name" not in properties
    assert properties["date_column"]["enum"] == ["date", None]
    assert properties["category_columns"]["items"]["enum"] == ["region"]
    assert "__import__" not in properties["operation"]["enum"]
    assert call["think"] is False
    assert call["stream"] is False
    assert call["options"] == {
        "temperature": 0,
        "num_ctx": 4096,
        "num_predict": 640,
    }


def test_schema_uses_only_actual_numeric_columns(tmp_path: Path) -> None:
    schema = build_derived_kpi_response_schema(_profile(tmp_path))
    properties = schema["properties"]["suggestions"]["items"]["properties"]

    assert properties["left_column"]["enum"] == ["revenue", "cost"]
    assert "date" not in properties["left_column"]["enum"]
    assert "region" not in properties["right_column"]["enum"]


def test_more_than_two_derived_suggestions_are_rejected(tmp_path: Path) -> None:
    content = json.dumps({"suggestions": [_suggestion()] * 3})

    with pytest.raises(DerivedKpiSuggestionError, match="invalid number"):
        parse_derived_kpi_response(content, profile=_profile(tmp_path))


def test_constant_and_identifier_like_numeric_columns_are_not_model_candidates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "employees.csv"
    path.write_text(
        "EmployeeNumber,StandardHours,DailyRate,MonthlyIncome\n"
        "1001,80,100,3000\n"
        "1002,80,120,3500\n"
        "1003,80,140,4000\n",
        encoding="utf-8",
    )
    schema = build_derived_kpi_response_schema(profile_csv(path))
    candidates = schema["properties"]["suggestions"]["items"]["properties"][
        "left_column"
    ]["enum"]

    assert candidates == ["DailyRate", "MonthlyIncome"]


def test_empty_rationale_name_and_format_are_normalized_safely(
    tmp_path: Path,
) -> None:
    response = _suggestion(
        operation="add",
        display_format="percentage",
        rationale=[],
    )

    batch = parse_derived_kpi_response(
        json.dumps({"suggestions": [response]}),
        profile=_profile(tmp_path),
    )
    suggestion = batch.suggestions[0]

    assert suggestion.metric.name == "revenue plus cost"
    assert suggestion.metric.display_format == "number"
    assert suggestion.rationale == (
        "The model supplied no rationale; review the formula and business meaning carefully.",
    )


def test_wide_profile_context_is_compact_and_candidate_list_is_bounded(
    tmp_path: Path,
) -> None:
    column_names = [f"metric_{index:02d}" for index in range(60)]
    path = tmp_path / "wide.csv"
    path.write_text(
        ",".join(column_names)
        + "\n"
        + "\n".join(
            ",".join(str(index + row) for index in range(60))
            for row in range(1, 4)
        )
        + "\n",
        encoding="utf-8",
    )
    profile = profile_csv(path)
    response = _suggestion(
        left_column="metric_00",
        right_column="metric_01",
        date_column=None,
        category_columns=[],
    )
    client = FakeChatClient(json.dumps({"suggestions": [response]}))

    generate_derived_kpi_suggestions(
        profile,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=5,
        client=client,
    )

    call = client.calls[0]
    summary = build_derived_kpi_profile_summary(profile)
    properties = call["format"]["properties"]["suggestions"]["items"]["properties"]
    combined_characters = len(call["messages"][0]["content"]) + len(
        call["messages"][1]["content"]
    ) + len(json.dumps(call["format"]))

    assert summary["numeric_columns_considered"] == 40
    assert summary["numeric_columns_omitted"] == 20
    assert len(properties["left_column"]["enum"]) == 40
    assert properties["left_column"]["enum"][0] == "metric_00"
    assert properties["left_column"]["enum"][-1] == "metric_39"
    assert combined_characters < 12_000


def test_fewer_than_two_numeric_columns_skips_ollama(tmp_path: Path) -> None:
    path = tmp_path / "one-number.csv"
    path.write_text("region,revenue\nA,10\nB,20\nA,30\n", encoding="utf-8")
    client = FakeChatClient()

    with pytest.raises(DerivedKpiSuggestionError, match="At least two"):
        generate_derived_kpi_suggestions(
            profile_csv(path),
            model="llama3.2:latest",
            host="http://127.0.0.1:11434",
            timeout_seconds=5,
            client=client,
        )

    assert client.calls == []


def test_local_model_failure_keeps_existing_kpis_available(tmp_path: Path) -> None:
    client = FakeChatClient(error=ConnectionError("private details"))

    with pytest.raises(DerivedKpiSuggestionError, match="Start Ollama") as captured:
        generate_derived_kpi_suggestions(
            _profile(tmp_path),
            model="llama3.2:latest",
            host="http://127.0.0.1:11434",
            timeout_seconds=5,
            client=client,
        )

    assert "private details" not in str(captured.value)
