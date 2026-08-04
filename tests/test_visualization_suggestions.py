"""Structured Ollama visualization-suggestion contract tests."""

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from insight_reporter.business_config import validate_business_configuration
from insight_reporter.dataset_profile import profile_csv
from insight_reporter.model_run_metrics import model_metrics_csv_path
from insight_reporter.visualization_suggestions import (
    VisualizationSuggestionError,
    build_visualization_profile_summary,
    build_visualization_suggestion_schema,
    generate_visualization_suggestion,
    parse_visualization_suggestion,
)


class FakeChatClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def chat(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(self.payload))
        )


def _profile_and_configuration(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "sales.csv"
    path.write_text(
        (
            "Order_Date,Region,Stage,Revenue,Cost,Order_ID\n"
            "2026-01-01,North,Lead,100,60,O-1\n"
            "2026-01-02,South,Qualified,120,70,O-2\n"
            "2026-02-01,North,Proposal,140,80,O-3\n"
            "2026-02-02,South,Won,160,90,O-4\n"
            "2026-03-01,North,Lead,180,100,O-5\n"
            "2026-03-02,South,Qualified,200,110,O-6\n"
            "2026-04-01,North,Proposal,220,120,O-7\n"
            "2026-04-02,South,Won,240,130,O-8\n"
        ),
        encoding="utf-8",
    )
    profile = profile_csv(path)
    configuration = validate_business_configuration(
        profile,
        dataset_id="a" * 32,
        primary_kpi="Revenue",
        secondary_kpis=["Cost"],
        kpi_direction="higher",
        date_column="Order_Date",
        category_columns=["Region", "Stage"],
        target_or_benchmark="",
        business_objective="Review sales performance.",
    )
    return profile, configuration


def _payload(selector: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Revenue by region",
        "purpose": "Compare regional revenue contribution.",
        "chart_type": "category_bar",
        "measure_selectors": [selector],
        "x_column": "Region",
        "series_column": None,
        "aggregation": "sum",
        "date_granularity": "month",
        "confidence": 0.92,
        "rationale": [
            "Region is a detected category.",
            "Revenue is a configured summable KPI.",
        ],
    }
    payload.update(overrides)
    return payload


def test_schema_and_profile_are_constrained_to_detected_inputs(
    tmp_path: Path,
) -> None:
    profile, configuration = _profile_and_configuration(tmp_path)
    schema = build_visualization_suggestion_schema(profile, configuration)
    summary = build_visualization_profile_summary(profile, configuration)
    properties = schema["properties"]
    selectors = properties["measure_selectors"]["items"]["enum"]
    semantic_types = {
        item["name"]: item["semantic_type"] for item in summary["columns"]
    }

    assert f"metric:{configuration.primary_metric_id}" in selectors
    assert "column:Order_ID" not in selectors
    assert properties["x_column"]["enum"] == [
        "Order_Date",
        "Region",
        "Stage",
        "Revenue",
        "Cost",
        None,
    ]
    assert semantic_types["Order_Date"] == "TEMPORAL"
    assert semantic_types["Region"] == "GEOGRAPHIC"
    assert semantic_types["Stage"] == "CATEGORICAL_ORDINAL"
    assert semantic_types["Revenue"].startswith("NUMERIC_")
    assert semantic_types["Order_ID"] == "IDENTIFIER"


def test_valid_model_suggestion_is_parsed_and_measured(tmp_path: Path) -> None:
    profile, configuration = _profile_and_configuration(tmp_path)
    selector = f"metric:{configuration.primary_metric_id}"
    client = FakeChatClient(_payload(selector))

    suggestion = generate_visualization_suggestion(
        profile,
        configuration=configuration,
        user_request="Show which region contributes the most revenue.",
        dataset_id="a" * 32,
        model="llama3.2:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=5,
        metrics_dir=tmp_path / "metrics",
        client=client,
    )

    assert suggestion.spec.chart_type == "category_bar"
    assert suggestion.spec.measure_selectors == (selector,)
    assert suggestion.spec.x_column == "Region"
    assert suggestion.confidence == 0.92
    call = client.calls[0]
    assert call["think"] is False
    assert call["stream"] is False
    assert call["options"] == {
        "temperature": 0,
        "num_ctx": 8192,
        "num_predict": 900,
    }
    prompt = json.dumps(call["messages"])
    assert "O-1" not in prompt
    assert "Show which region" in prompt
    with model_metrics_csv_path(tmp_path / "metrics").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        [metrics_row] = list(csv.DictReader(handle))
    assert metrics_row["task_type"] == "visualization_suggestions"
    assert metrics_row["prompt_version"] == "visualization_suggestions.v1"
    assert metrics_row["status"] == "validated"


@pytest.mark.parametrize(
    "overrides",
    [
        {"measure_selectors": ["column:invented"]},
        {"x_column": "invented"},
        {"chart_type": "donut", "aggregation": "mean"},
        {"chart_type": "combo", "measure_selectors": ["count:records"]},
        {
            "chart_type": "heatmap",
            "x_column": "Region",
            "series_column": "Region",
        },
    ],
)
def test_incompatible_or_hallucinated_suggestions_are_rejected(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    profile, configuration = _profile_and_configuration(tmp_path)
    selector = f"metric:{configuration.primary_metric_id}"

    with pytest.raises(VisualizationSuggestionError):
        parse_visualization_suggestion(
            json.dumps(_payload(selector, **overrides)),
            profile=profile,
            configuration=configuration,
            user_request="Recommend a chart.",
        )
