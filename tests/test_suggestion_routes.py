"""Plain-HTML route tests for optional AI-assisted configuration."""

import io
import json
import re
from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient

from insight_reporter.configuration_suggestions import (
    ConfigurationSuggestion,
    ConfigurationSuggestionError,
    SuggestionBatch,
)


def _upload(client: FlaskClient):  # type: ignore[no-untyped-def]
    content = (
        b"customer_id,date,region,revenue\n"
        b"C-1,2026-01-01,North,100\n"
        b"C-2,2026-01-02,South,120\n"
        b"C-3,2026-01-03,North,140\n"
    )
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(content), "business.csv", "text/csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def _dataset_id(response_data: bytes) -> str:
    match = re.search(rb"<dd>([0-9a-f]{32})\.csv</dd>", response_data)
    assert match is not None
    return match.group(1).decode("ascii")


def _suggestion(
    objective: str = "Evaluate regional revenue performance.",
    *,
    primary_kpi: str = "revenue",
    direction: str = "higher",
    title: str = "Revenue performance",
    aggregation: str = "sum",
    display_format: str = "currency",
    target_scope: str = "period",
) -> ConfigurationSuggestion:
    return ConfigurationSuggestion(
        title=title,
        primary_kpi=primary_kpi,
        kpi_direction=direction,
        aggregation=aggregation,
        display_format=display_format,
        date_column="date",
        category_columns=("region",),
        target_or_benchmark=None,
        target_scope=target_scope,
        business_objective=objective,
        confidence=0.91,
        rationale=("Revenue is a valid numeric KPI candidate.",),
    )


def _upload_multi_kpi(client: FlaskClient):  # type: ignore[no-untyped-def]
    content = (
        b"date,region,revenue,cost,units\n"
        b"2026-01-01,North,100,60,4\n"
        b"2026-01-02,South,120,70,5\n"
        b"2026-02-01,North,140,80,6\n"
        b"2026-02-02,South,160,90,7\n"
    )
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(content), "multi-kpi.csv", "text/csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_upload_does_not_call_ollama(client: FlaskClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def unexpected_call(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("Ollama must run only after explicit user action")

    monkeypatch.setattr(
        "insight_reporter.routes.generate_configuration_suggestions",
        unexpected_call,
    )

    response = _upload(client)

    assert response.status_code == 200
    assert b"Generate AI suggestions" in response.data


def test_generate_suggestions_displays_validated_cards(
    client: FlaskClient, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    upload_response = _upload(client)
    dataset_id = _dataset_id(upload_response.data)
    monkeypatch.setattr(
        "insight_reporter.routes.generate_configuration_suggestions",
        lambda *_args, **_kwargs: SuggestionBatch((_suggestion(),), rejected_count=1),
    )

    response = client.post(f"/suggest/{dataset_id}", follow_redirects=True)

    assert response.status_code == 200
    assert b"Suggested configurations" in response.data
    assert b"Revenue performance" in response.data
    assert b"<dd>sum</dd>" in response.data
    assert b"<dd>currency</dd>" in response.data
    assert b"Each period" in response.data
    assert b"91%" in response.data
    assert b"Use this suggestion" in response.data
    assert b"1 invalid AI suggestion(s)" in response.data


def test_ollama_failure_keeps_manual_configuration_available(
    client: FlaskClient, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    upload_response = _upload(client)
    dataset_id = _dataset_id(upload_response.data)

    def unavailable(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise ConfigurationSuggestionError("Local Ollama suggestions are unavailable.")

    monkeypatch.setattr(
        "insight_reporter.routes.generate_configuration_suggestions",
        unavailable,
    )

    response = client.post(f"/suggest/{dataset_id}", follow_redirects=True)

    assert response.status_code == 503
    assert b"AI suggestions unavailable" in response.data
    assert b"Confirm business configuration" in response.data
    assert b"Select a KPI" in response.data


def test_use_suggestion_prefills_existing_editable_form(client: FlaskClient) -> None:
    upload_response = _upload(client)
    dataset_id = _dataset_id(upload_response.data)

    response = client.post(
        f"/review-suggestion/{dataset_id}",
        data={
            "primary_kpi": "revenue",
            "kpi_direction": "higher",
            "aggregation": "sum",
            "display_format": "currency",
            "date_column": "date",
            "category_columns": ["region"],
            "target_or_benchmark": "",
            "target_scope": "period",
            "business_objective": "Evaluate regional revenue performance.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"AI suggestion loaded" in response.data
    assert b"Evaluate regional revenue performance." in response.data
    assert re.search(rb'value="revenue"\s+selected', response.data) is not None
    assert re.search(rb'value="date"\s+selected', response.data) is not None
    assert re.search(rb'value="region"\s+checked', response.data) is not None


def test_tampered_posted_suggestion_is_rejected(client: FlaskClient) -> None:
    upload_response = _upload(client)
    dataset_id = _dataset_id(upload_response.data)

    response = client.post(
        f"/review-suggestion/{dataset_id}",
        data={
            "primary_kpi": "invented_profit",
            "kpi_direction": "higher",
            "date_column": "date",
            "business_objective": "Tampered configuration.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 400
    assert b"Configuration rejected" in response.data
    assert b"measurable KPI" in response.data


def test_suggestion_text_is_html_escaped(client: FlaskClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    upload_response = _upload(client)
    dataset_id = _dataset_id(upload_response.data)
    monkeypatch.setattr(
        "insight_reporter.routes.generate_configuration_suggestions",
        lambda *_args, **_kwargs: SuggestionBatch(
            (_suggestion("<script>alert(1)</script>"),), rejected_count=0
        ),
    )

    response = client.post(f"/suggest/{dataset_id}", follow_redirects=True)
    page = response.data.decode("utf-8")

    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page


def test_ai_can_suggest_and_review_an_additional_source_kpi(
    app: Flask,
    client: FlaskClient,
    monkeypatch,
) -> None:
    uploaded = _upload_multi_kpi(client)
    dataset_id = _dataset_id(uploaded.data)
    client.post(
        f"/configure/{dataset_id}",
        data={
            "primary_kpi": "revenue",
            "kpi_direction": "higher",
            "date_column": "date",
            "category_columns": ["region"],
            "business_objective": "Track revenue performance.",
        },
        follow_redirects=True,
    )
    captured: dict[str, object] = {}
    cost_suggestion = _suggestion(
        "Control operating costs by region over time.",
        primary_kpi="cost",
        direction="lower",
        title="Cost control",
        aggregation="mean",
        display_format="currency",
        target_scope="segment",
    )

    def suggest(*_args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return SuggestionBatch((cost_suggestion,), rejected_count=0)

    monkeypatch.setattr(
        "insight_reporter.routes.generate_configuration_suggestions",
        suggest,
    )

    profile_page = client.get(f"/dataset/{dataset_id}")
    assert b"Suggest additional source KPIs" in profile_page.data
    suggested = client.post(
        f"/suggest/{dataset_id}",
        follow_redirects=True,
    )
    assert captured["excluded_kpis"] == ("revenue",)
    assert b"Suggested additional KPI configurations" in suggested.data
    assert b"Review and add this KPI" in suggested.data
    assert b'<input type="hidden" name="aggregation" value="mean">' in suggested.data
    assert b'<input type="hidden" name="target_scope" value="segment">' in suggested.data
    reviewed = client.post(
        f"/review-suggestion/{dataset_id}",
        data={
            "primary_kpi": "cost",
            "kpi_direction": "lower",
            "aggregation": "mean",
            "display_format": "currency",
            "date_column": "date",
            "category_columns": ["region"],
            "target_or_benchmark": "",
            "target_scope": "segment",
            "business_objective": (
                "Control operating costs by region over time."
            ),
        },
        follow_redirects=True,
    )
    assert reviewed.status_code == 200
    assert b"Additional KPI suggestion loaded" in reviewed.data
    assert re.search(rb'value="cost"[^>]*checked', reviewed.data) is not None
    assert re.search(rb'value="mean"[^>]*selected', reviewed.data) is not None
    assert re.search(rb'value="currency"[^>]*selected', reviewed.data) is not None
    assert re.search(rb'value="segment"[^>]*selected', reviewed.data) is not None
    assert b"Control operating costs by region over time." in reviewed.data

    added = client.post(
        f"/configure/{dataset_id}",
        data={
            "source_kpis": ["cost"],
            "kpi_direction": "lower",
            "aggregation": "mean",
            "display_format": "currency",
            "target_scope": "segment",
            "context_submitted": "yes",
            "date_column": "date",
            "category_columns": ["region"],
            "business_objective": (
                "Control operating costs by region over time."
            ),
        },
        follow_redirects=True,
    )

    assert added.status_code == 200
    path = Path(app.config["CONFIGURATION_DIR"]) / f"{dataset_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [metric["name"] for metric in payload["metrics"]] == [
        "revenue",
        "cost",
    ]
    primary = next(
        metric
        for metric in payload["metrics"]
        if metric["metric_id"] == payload["primary_metric_id"]
    )
    assert primary["name"] == "revenue"
    assert payload["business_objective"] == (
        "Control operating costs by region over time."
    )
    assert payload["metrics"][1]["aggregation"] == "mean"
    assert payload["metrics"][1]["display_format"] == "currency"
    assert payload["metrics"][1]["target_scope"] == "segment"
