"""Structured local-Ollama suggestion and validation tests."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from insight_reporter.configuration_suggestions import (
    SUGGESTION_RESPONSE_SCHEMA,
    ConfigurationSuggestionError,
    build_profile_summary,
    build_suggestion_response_schema,
    generate_configuration_suggestions,
    parse_suggestion_response,
)
from insight_reporter.dataset_profile import DatasetProfile, profile_csv


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


def _profile(tmp_path: Path) -> DatasetProfile:
    path = tmp_path / "business.csv"
    path.write_text(
        (
            "customer_id,date,region,revenue,cost\n"
            "C-1,2026-01-01,North,100,75\n"
            "C-2,2026-01-02,South,120,80\n"
            "C-3,2026-01-03,North,140,90\n"
        ),
        encoding="utf-8",
    )
    return profile_csv(path)


def _suggestion(**overrides: object) -> dict[str, object]:
    suggestion: dict[str, object] = {
        "title": "Revenue performance",
        "primary_kpi": "revenue",
        "kpi_direction": "higher",
        "date_column": "date",
        "category_columns": ["region"],
        "target_or_benchmark": None,
        "business_objective": "Evaluate revenue performance by region over time.",
        "confidence": 0.91,
        "rationale": [
            "Revenue is a nonconstant numeric KPI candidate.",
            "Date supports time-based analysis.",
        ],
    }
    suggestion.update(overrides)
    return suggestion


def _response(*suggestions: dict[str, object]) -> str:
    return json.dumps({"suggestions": list(suggestions)})


def test_valid_structured_suggestion_is_accepted(tmp_path: Path) -> None:
    profile = _profile(tmp_path)

    batch = parse_suggestion_response(
        _response(_suggestion()),
        profile=profile,
        dataset_id="a" * 32,
    )

    assert len(batch.suggestions) == 1
    assert batch.rejected_count == 0
    assert batch.suggestions[0].primary_kpi == "revenue"
    assert batch.suggestions[0].category_columns == ("region",)
    assert batch.suggestions[0].target_or_benchmark is None


def test_hallucinated_columns_are_discarded_while_valid_suggestions_remain(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path)

    batch = parse_suggestion_response(
        _response(
            _suggestion(primary_kpi="invented_profit"),
            _suggestion(title="Cost control", primary_kpi="cost", kpi_direction="lower"),
        ),
        profile=profile,
        dataset_id="a" * 32,
    )

    assert len(batch.suggestions) == 1
    assert batch.suggestions[0].primary_kpi == "cost"
    assert batch.rejected_count == 1


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        json.dumps([]),
        json.dumps({"suggestions": []}),
        json.dumps({"suggestions": [_suggestion()] * 4}),
        json.dumps({"suggestions": [_suggestion()], "unexpected": True}),
    ],
)
def test_malformed_response_shapes_are_rejected(tmp_path: Path, content: str) -> None:
    with pytest.raises(ConfigurationSuggestionError):
        parse_suggestion_response(
            content,
            profile=_profile(tmp_path),
            dataset_id="a" * 32,
        )


def test_all_invalid_suggestions_trigger_manual_fallback(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationSuggestionError, match="no valid suggestions"):
        parse_suggestion_response(
            _response(_suggestion(primary_kpi="invented_profit")),
            profile=_profile(tmp_path),
            dataset_id="a" * 32,
        )


def test_model_may_not_invent_target(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationSuggestionError, match="no valid suggestions"):
        parse_suggestion_response(
            _response(_suggestion(target_or_benchmark=500)),
            profile=_profile(tmp_path),
            dataset_id="a" * 32,
        )


def test_unexpected_suggestion_fields_are_rejected(tmp_path: Path) -> None:
    invalid = _suggestion()
    invalid["extra"] = "not allowed"

    with pytest.raises(ConfigurationSuggestionError, match="no valid suggestions"):
        parse_suggestion_response(
            _response(invalid),
            profile=_profile(tmp_path),
            dataset_id="a" * 32,
        )


def test_prompt_contains_profile_metadata_but_not_raw_rows(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    client = FakeChatClient(_response(_suggestion()))

    generate_configuration_suggestions(
        profile,
        dataset_id="a" * 32,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=5,
        client=client,
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    user_prompt = call["messages"][1]["content"]
    assert "revenue" in user_prompt
    assert "candidate_columns" in user_prompt
    assert "C-1" not in user_prompt
    assert "North" not in user_prompt
    assert "preview_rows" not in user_prompt
    assert call["format"] == build_suggestion_response_schema(profile)
    assert call["options"] == {"temperature": 0}
    assert call["stream"] is False
    assert call["think"] is False


def test_existing_kpis_are_excluded_from_repeat_suggestions(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path)
    client = FakeChatClient(
        _response(
            _suggestion(
                title="Cost control",
                primary_kpi="cost",
                kpi_direction="lower",
            )
        )
    )

    batch = generate_configuration_suggestions(
        profile,
        dataset_id="a" * 32,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=5,
        client=client,
        excluded_kpis=("revenue",),
    )

    assert batch.suggestions[0].primary_kpi == "cost"
    call = client.calls[0]
    kpi_enum = call["format"]["properties"]["suggestions"]["items"][
        "properties"
    ]["primary_kpi"]["enum"]
    assert kpi_enum == ["cost"]
    profile_payload = json.loads(
        call["messages"][1]["content"].split("\n", maxsplit=1)[1]
    )
    assert profile_payload["candidate_columns"]["kpi"] == ["cost"]


def test_excluded_kpi_returned_by_model_is_rejected(tmp_path: Path) -> None:
    profile = _profile(tmp_path)

    with pytest.raises(ConfigurationSuggestionError, match="no valid"):
        parse_suggestion_response(
            _response(_suggestion(primary_kpi="revenue")),
            profile=profile,
            dataset_id="a" * 32,
            allowed_kpis=("cost",),
        )


def test_profile_summary_never_contains_preview_rows(tmp_path: Path) -> None:
    summary = build_profile_summary(_profile(tmp_path))

    assert "preview_rows" not in summary
    assert "source_sha256" not in summary
    assert summary["row_count"] == 3


def test_local_ollama_failure_returns_safe_fallback_error(tmp_path: Path) -> None:
    client = FakeChatClient(error=ConnectionError("private connection details"))

    with pytest.raises(ConfigurationSuggestionError, match="Start Ollama") as captured:
        generate_configuration_suggestions(
            _profile(tmp_path),
            dataset_id="a" * 32,
            model="llama3.2:latest",
            host="http://127.0.0.1:11434",
            timeout_seconds=5,
            client=client,
        )

    assert "private connection details" not in str(captured.value)


def test_model_schema_avoids_grammar_constraints_rejected_by_llama32() -> None:
    unsupported = {
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "uniqueItems",
    }

    def schema_keys(value: object) -> set[str]:
        if isinstance(value, dict):
            keys = set(value)
            return keys.union(*(schema_keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(schema_keys(item) for item in value))
        return set()

    assert schema_keys(SUGGESTION_RESPONSE_SCHEMA).isdisjoint(unsupported)


def test_model_schema_forces_null_when_dataset_has_no_date(tmp_path: Path) -> None:
    path = tmp_path / "no-date.csv"
    path.write_text(
        "region,revenue\nNorth,100\nSouth,120\nNorth,140\n",
        encoding="utf-8",
    )
    profile = profile_csv(path)

    schema = build_suggestion_response_schema(profile)
    suggestion_properties = schema["properties"]["suggestions"]["items"]["properties"]

    assert suggestion_properties["date_column"] == {"type": "null"}
    assert suggestion_properties["primary_kpi"]["enum"] == list(
        profile.kpi_candidates
    )
    assert suggestion_properties["category_columns"]["items"]["enum"] == list(
        profile.category_candidates
    )


def test_no_date_fake_model_call_uses_null_and_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "no-date.csv"
    path.write_text(
        "region,revenue\nNorth,100\nSouth,120\nNorth,140\n",
        encoding="utf-8",
    )
    profile = profile_csv(path)
    client = FakeChatClient(_response(_suggestion(date_column=None)))

    batch = generate_configuration_suggestions(
        profile,
        dataset_id="a" * 32,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=5,
        client=client,
    )

    assert batch.suggestions[0].date_column is None
    assert client.calls[0]["format"]["properties"]["suggestions"]["items"][
        "properties"
    ]["date_column"] == {"type": "null"}


def test_model_schema_forces_empty_categories_when_none_are_detected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "numeric-only.csv"
    path.write_text(
        "temperature,pressure\n20,100\n21,101\n22,102\n",
        encoding="utf-8",
    )
    profile = profile_csv(path)
    client = FakeChatClient(
        _response(
            _suggestion(
                primary_kpi="temperature",
                date_column=None,
                category_columns=[],
            )
        )
    )

    batch = generate_configuration_suggestions(
        profile,
        dataset_id="a" * 32,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=5,
        client=client,
    )
    suggestion_properties = client.calls[0]["format"]["properties"]["suggestions"][
        "items"
    ]["properties"]

    assert profile.category_candidates == ()
    assert suggestion_properties["category_columns"] == {"const": []}
    assert batch.suggestions[0].category_columns == ()
