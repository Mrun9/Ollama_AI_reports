"""Plain-HTML route tests for optional AI-assisted configuration."""

import io
import re

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
) -> ConfigurationSuggestion:
    return ConfigurationSuggestion(
        title="Revenue performance",
        primary_kpi="revenue",
        kpi_direction="higher",
        date_column="date",
        category_columns=("region",),
        target_or_benchmark=None,
        business_objective=objective,
        confidence=0.91,
        rationale=("Revenue is a valid numeric KPI candidate.",),
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
            "date_column": "date",
            "category_columns": ["region"],
            "target_or_benchmark": "",
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
