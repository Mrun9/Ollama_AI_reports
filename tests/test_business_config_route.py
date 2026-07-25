"""End-to-end Flask workflow tests for profiling and confirmation."""

import io
import json
import re
from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient


def _upload_business_csv(client: FlaskClient):  # type: ignore[no-untyped-def]
    content = (
        b"customer_id,date,region,revenue\n"
        b"C-1,2026-01-01,North,100\n"
        b"C-2,2026-01-02,South,120\n"
        b"C-3,2026-02-01,North,140\n"
        b"C-4,2026-02-02,South,160\n"
        b"C-5,2026-03-01,North,180\n"
        b"C-6,2026-03-02,South,200\n"
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


def test_upload_displays_profile_and_candidate_form(client: FlaskClient) -> None:
    response = _upload_business_csv(client)

    assert response.status_code == 200
    assert b"Dataset profile" in response.data
    assert b"date/time" in response.data
    assert b"identifier" in response.data
    assert b"numeric" in response.data
    assert b"Confirm business configuration" in response.data
    assert b'value="revenue"' in response.data
    assert b'value="date"' in response.data
    assert b'value="region"' in response.data


def test_confirmed_configuration_is_validated_and_persisted(
    app: Flask, client: FlaskClient
) -> None:
    upload_response = _upload_business_csv(client)
    dataset_id = _dataset_id(upload_response.data)

    response = client.post(
        f"/configure/{dataset_id}",
        data={
            "primary_kpi": "revenue",
            "kpi_direction": "higher",
            "date_column": "date",
            "category_columns": ["region"],
            "target_or_benchmark": "150",
            "business_objective": "Increase regional revenue.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Business configuration saved" in response.data
    assert b"Generate deterministic insights" in response.data
    configuration_path = Path(app.config["CONFIGURATION_DIR"]) / f"{dataset_id}.json"
    configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
    assert configuration["dataset_id"] == dataset_id
    assert configuration["schema_version"] == 4
    assert configuration["metrics"][0]["name"] == "revenue"
    assert configuration["metrics"][0]["target_or_benchmark"] == 150
    assert configuration["date_column"]["column"] == "date"
    assert configuration["category_columns"][0]["column"] == "region"


def test_deterministic_insights_are_generated_and_saved_without_ollama(
    app: Flask, client: FlaskClient, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    upload_response = _upload_business_csv(client)
    dataset_id = _dataset_id(upload_response.data)
    configured = client.post(
        f"/configure/{dataset_id}",
        data={
            "primary_kpi": "revenue",
            "kpi_direction": "higher",
            "date_column": "date",
            "category_columns": ["region"],
            "target_or_benchmark": "150",
            "business_objective": "Increase regional revenue.",
        },
        follow_redirects=True,
    )
    assert configured.status_code == 200

    def forbidden_ollama_call(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("Milestone 3 must not call Ollama")

    monkeypatch.setattr(
        "insight_reporter.routes.generate_configuration_suggestions",
        forbidden_ollama_call,
    )
    response = client.post(f"/insights/{dataset_id}", follow_redirects=True)

    assert response.status_code == 200
    assert b"Deterministic insights generated" in response.data
    assert b"Ollama was not used" in response.data
    insight_path = Path(app.config["INSIGHT_DIR"]) / f"{dataset_id}.json"
    payload = json.loads(insight_path.read_text(encoding="utf-8"))
    assert payload["dataset_id"] == dataset_id
    assert payload["insights"]
    assert any(item["type"] == "benchmark_breach" for item in payload["insights"])
    evidence_path = Path(app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert len(evidence["records"]) == len(payload["insights"])
    chart_record = next(
        record for record in evidence["records"] if record["chart"] is not None
    )
    chart = client.get(
        f"/evidence/{dataset_id}/{chart_record['id']}/chart"
    )
    assert chart.status_code == 200
    assert chart.mimetype == "image/png"
    assert chart.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert client.get(f"/evidence/{dataset_id}/../../escape/chart").status_code == 404


def test_tampered_selection_is_rejected_without_configuration_file(
    app: Flask, client: FlaskClient
) -> None:
    upload_response = _upload_business_csv(client)
    dataset_id = _dataset_id(upload_response.data)

    response = client.post(
        f"/configure/{dataset_id}",
        data={
            "primary_kpi": "customer_id",
            "kpi_direction": "higher",
            "date_column": "date",
            "category_columns": ["region"],
            "business_objective": "Invalid attempt.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 400
    assert b"Configuration rejected" in response.data
    assert b"measurable KPI" in response.data
    assert not (Path(app.config["CONFIGURATION_DIR"]) / f"{dataset_id}.json").exists()


def test_business_objective_is_escaped(client: FlaskClient) -> None:
    upload_response = _upload_business_csv(client)
    dataset_id = _dataset_id(upload_response.data)

    response = client.post(
        f"/configure/{dataset_id}",
        data={
            "primary_kpi": "revenue",
            "kpi_direction": "higher",
            "date_column": "date",
            "business_objective": "<script>alert(1)</script>",
        },
        follow_redirects=True,
    )
    page = response.data.decode("utf-8")

    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page
