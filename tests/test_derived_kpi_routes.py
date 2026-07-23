"""End-to-end route tests for optional derived KPI selection."""

import io
import json
import re
from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient

from insight_reporter.dataset_profile import profile_csv
from insight_reporter.derived_kpi_suggestions import (
    DerivedKpiSuggestion,
    DerivedKpiSuggestionBatch,
)
from insight_reporter.derived_metrics import validate_derived_metric


def _upload(client: FlaskClient):  # type: ignore[no-untyped-def]
    content = (
        b"date,region,revenue,cost\n"
        b"2026-01-01,North,100,60\n"
        b"2026-01-02,South,200,120\n"
        b"2026-02-01,North,150,80\n"
        b"2026-02-02,South,250,140\n"
        b"2026-03-01,North,180,90\n"
        b"2026-03-02,South,300,160\n"
    )
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(content), "sales.csv", "text/csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def _dataset_id(response_data: bytes) -> str:
    match = re.search(rb"<dd>([0-9a-f]{32})\.csv</dd>", response_data)
    assert match is not None
    return match.group(1).decode("ascii")


def _formula_form() -> dict[str, object]:
    return {
        "name": "Profit",
        "formula": "[revenue] - [cost]",
        "calculation_level": "row",
        "aggregation": "sum",
        "display_format": "currency",
        "kpi_direction": "higher",
    }


def test_upload_keeps_source_kpis_and_does_not_generate_derived_automatically(
    client: FlaskClient, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    def forbidden(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("Derived suggestions must require explicit user action")

    monkeypatch.setattr(
        "insight_reporter.routes.generate_derived_kpi_suggestions", forbidden
    )

    response = _upload(client)

    assert response.status_code == 200
    assert b'value="revenue"' in response.data
    assert b'value="cost"' in response.data
    assert b"Suggest two derived KPIs" in response.data
    assert b"Build a derived KPI manually" in response.data


def test_on_demand_suggestion_review_confirmation_and_insights_workflow(
    app: Flask, client: FlaskClient, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    upload_response = _upload(client)
    dataset_id = _dataset_id(upload_response.data)
    csv_path = Path(app.config["UPLOAD_DIR"]) / f"{dataset_id}.csv"
    profile = profile_csv(csv_path)
    metric = validate_derived_metric(
        profile,
        name="Profit",
        operation="subtract",
        left_column="revenue",
        right_column="cost",
        aggregation="sum",
        display_format="currency",
    )
    suggestion = DerivedKpiSuggestion(
        metric=metric,
        kpi_direction="higher",
        date_column="date",
        category_columns=("region",),
        benchmark_strategy="dataset_mean",
        business_objective="Evaluate profit by region over time.",
        confidence=0.9,
        rationale=("Profit adds business meaning beyond either source column.",),
    )
    monkeypatch.setattr(
        "insight_reporter.routes.generate_derived_kpi_suggestions",
        lambda *_args, **_kwargs: DerivedKpiSuggestionBatch((suggestion,), 0),
    )

    suggestions = client.post(
        f"/suggest-derived/{dataset_id}", follow_redirects=True
    )
    assert suggestions.status_code == 200
    assert b"Derived KPI suggestions" in suggestions.data
    assert b"revenue - cost" in suggestions.data
    assert b"Dataset mean (calculated by Python)" in suggestions.data
    assert b"Evaluate profit by region over time." in suggestions.data

    review_form = {
        **_formula_form(),
        "date_column": "date",
        "category_columns": ["region"],
        "benchmark_strategy": "dataset_mean",
        "target_or_benchmark": "",
        "business_objective": "Evaluate profit by region over time.",
    }
    review = client.post(
        f"/review-derived/{dataset_id}", data=review_form, follow_redirects=True
    )
    assert review.status_code == 200
    assert b"Python calculation preview" in review.data
    assert b"Valid results" in review.data
    assert b'id="derived-name"' in review.data
    assert b'id="derived-formula"' in review.data
    assert b'id="calculation-level"' in review.data
    assert b"Recalculate Python preview" in review.data
    assert b'id="derived-aggregation"' in review.data
    assert b'value="sum"' in review.data
    assert b'value="mean"' in review.data
    assert b'value="88.33333333"' in review.data
    assert b"Evaluate profit by region over time." in review.data
    assert b"Confirm derived KPI configuration" in review.data

    configuration_form = {
        **_formula_form(),
        "date_column": "date",
        "category_columns": ["region"],
        "target_or_benchmark": "50",
        "business_objective": "Evaluate profit by region over time.",
    }
    configured = client.post(
        f"/configure-derived/{dataset_id}",
        data=configuration_form,
        follow_redirects=True,
    )
    assert configured.status_code == 200
    assert b"Business configuration saved" in configured.data
    assert b"Formula" in configured.data
    configuration_path = Path(app.config["CONFIGURATION_DIR"]) / f"{dataset_id}.json"
    payload = json.loads(configuration_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 4
    assert payload["metrics"][0]["metric_type"] == "derived"
    assert payload["metrics"][0]["name"] == "Profit"
    assert payload["metrics"][0]["derived_metric"]["schema_version"] == 2

    insights = client.post(f"/insights/{dataset_id}", follow_redirects=True)
    assert insights.status_code == 200
    insight_path = Path(app.config["INSIGHT_DIR"]) / f"{dataset_id}.json"
    evidence = json.loads(insight_path.read_text(encoding="utf-8"))
    assert evidence["metric_definition"]["metric_type"] == "derived"
    assert evidence["metric_definition"]["formula"] == "[revenue] - [cost]"
    assert evidence["insights"]


def test_tampered_derived_formula_is_rejected(client: FlaskClient) -> None:
    upload_response = _upload(client)
    dataset_id = _dataset_id(upload_response.data)
    tampered = _formula_form()
    tampered["formula"] = "__import__('os').system('id')"

    response = client.post(
        f"/review-derived/{dataset_id}", data=tampered, follow_redirects=True
    )

    assert response.status_code == 400
    assert b"Changes rejected" in response.data
    assert b"Unsupported formula character" in response.data
    assert b"Recalculate Python preview" in response.data


def test_user_can_change_formula_fields_and_recalculate_preview(
    client: FlaskClient,
) -> None:
    upload_response = _upload(client)
    dataset_id = _dataset_id(upload_response.data)
    edited_formula = {
        "name": "Revenue per cost",
        "formula": "SUM([revenue]) / SUM([cost])",
        "calculation_level": "aggregate",
        "aggregation": "formula",
        "display_format": "number",
        "kpi_direction": "higher",
        "date_column": "date",
        "category_columns": ["region"],
        "benchmark_strategy": "none",
        "target_or_benchmark": "",
        "business_objective": "Evaluate revenue efficiency by region.",
    }

    response = client.post(
        f"/review-derived/{dataset_id}", data=edited_formula, follow_redirects=True
    )

    assert response.status_code == 200
    assert b"SUM([revenue]) / SUM([cost])" in response.data
    assert b'id="derived-formula"' in response.data
    assert b'id="calculation-level"' in response.data
    assert b"Confirm derived KPI configuration" in response.data


def test_derived_suggestion_text_is_html_escaped(
    app: Flask, client: FlaskClient, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    upload_response = _upload(client)
    dataset_id = _dataset_id(upload_response.data)
    profile = profile_csv(Path(app.config["UPLOAD_DIR"]) / f"{dataset_id}.csv")
    metric = validate_derived_metric(
        profile,
        name="<script>alert(1)</script>",
        operation="subtract",
        left_column="revenue",
        right_column="cost",
        aggregation="sum",
        display_format="currency",
    )
    suggestion = DerivedKpiSuggestion(
        metric=metric,
        kpi_direction="higher",
        date_column="date",
        category_columns=("region",),
        benchmark_strategy="dataset_mean",
        business_objective="Review the derived KPI.",
        confidence=0.8,
        rationale=("Safe rationale.",),
    )
    monkeypatch.setattr(
        "insight_reporter.routes.generate_derived_kpi_suggestions",
        lambda *_args, **_kwargs: DerivedKpiSuggestionBatch((suggestion,), 0),
    )

    response = client.post(
        f"/suggest-derived/{dataset_id}", follow_redirects=True
    )
    page = response.data.decode("utf-8")

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page


def test_multiple_source_kpis_can_be_edited_and_reordered(
    app: Flask, client: FlaskClient
) -> None:
    upload_response = _upload(client)
    dataset_id = _dataset_id(upload_response.data)
    configured = client.post(
        f"/configure/{dataset_id}",
        data={
            "primary_kpi": "revenue",
            "secondary_kpis": ["cost"],
            "kpi_direction": "higher",
            "date_column": "date",
            "category_columns": ["region"],
            "target_or_benchmark": "",
            "business_objective": "Compare revenue and cost.",
        },
        follow_redirects=True,
    )
    assert configured.status_code == 200
    path = Path(app.config["CONFIGURATION_DIR"]) / f"{dataset_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    cost = next(metric for metric in payload["metrics"] if metric["name"] == "cost")

    edited = client.post(
        f"/configuration/{dataset_id}/metric",
        data={
            "metric_id": cost["metric_id"],
            "kpi_direction": "lower",
            "target_or_benchmark": "100",
        },
        follow_redirects=True,
    )
    selected = client.post(
        f"/configuration/{dataset_id}/primary",
        data={"metric_id": cost["metric_id"]},
        follow_redirects=True,
    )

    assert edited.status_code == 200
    assert selected.status_code == 200
    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated["primary_metric_id"] == cost["metric_id"]
    saved_cost = next(
        metric for metric in updated["metrics"] if metric["name"] == "cost"
    )
    assert saved_cost["kpi_direction"] == "lower"
    assert saved_cost["target_or_benchmark"] == 100
