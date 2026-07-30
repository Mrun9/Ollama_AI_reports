"""Milestone 5A report-configuration route and HTML-safety tests."""

import io
import json
import re
from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient

from insight_reporter.report_narration import (
    ReportNarrationError,
    generate_narrated_report,
    regenerate_generated_story,
)


class _FakeNarrationClient:
    def chat(self, **kwargs: object) -> object:
        schema = kwargs["format"]
        properties = schema["properties"]
        if "points" in properties:
            prompt = kwargs["messages"][1]["content"]
            report_payload = json.loads(prompt.split("\n", maxsplit=1)[1])
            story = report_payload["stories"][0]
            fact = story["available_fact_references"][0]
            business_context = story.get("verified_business_context", [])
            context = (
                business_context[0]["value"]
                if business_context
                else "the verified scope"
            )
            qualifiers = (
                "Primary",
                "Secondary",
                "Additional",
                "Related",
                "Supporting",
            )
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "points": [
                                {
                                    "finding": (
                                        f"{qualifier} finding for "
                                        f"{story['metric']} is "
                                        f"{fact['display_value']} for "
                                        f"{context}."
                                    ),
                                    "business_implication": (
                                        f"This {story['metric']} result is "
                                        "relevant to the objective."
                                    ),
                                    "recommended_action": (
                                        "Review this result and monitor the "
                                        "next validated period."
                                    ),
                                    "story_ids": [story["story_id"]],
                                    "fact_references": [fact["reference"]],
                                }
                                for qualifier in qualifiers
                            ]
                        }
                    )
                }
            }
        story_id = properties["story_id"]["enum"][0]
        fact_references = properties["fact_references"]["items"].get(
            "enum",
            [],
        )
        return {
            "message": {
                "content": json.dumps(
                    {
                        "story_id": story_id,
                        "headline": "A verified pattern merits review",
                        "finding": (
                            "The selected evidence highlights a useful "
                            "descriptive comparison."
                        ),
                        "interpretation": (
                            "The combined evidence is relevant to the "
                            "configured objective."
                        ),
                        "follow_up": (
                            "Review whether the pattern persists in future "
                            "validated data."
                        ),
                        "caveat": (
                            "The evidence is descriptive and does not "
                            "establish causation."
                        ),
                        "fact_references": fact_references[:2],
                    }
                )
            }
        }


def _upload_and_configure(client: FlaskClient) -> str:
    content = (
        b"date,segment,revenue,cost\n"
        b"2026-01-01,North,100,60\n"
        b"2026-01-02,South,200,120\n"
        b"2026-02-01,North,150,80\n"
        b"2026-02-02,South,250,\n"
        b"2026-03-01,North,180,90\n"
        b"2026-03-02,South,300,160\n"
    )
    uploaded = client.post(
        "/upload",
        data={"file": (io.BytesIO(content), "report.csv", "text/csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    match = re.search(rb"<dd>([0-9a-f]{32})\.csv</dd>", uploaded.data)
    assert match is not None
    dataset_id = match.group(1).decode("ascii")
    configured = client.post(
        f"/configure/{dataset_id}",
        data={
            "primary_kpi": "revenue",
            "secondary_kpis": ["cost"],
            "kpi_direction": "higher",
            "date_column": "date",
            "category_columns": ["segment"],
            "target_or_benchmark": "",
            "business_objective": "Review revenue and cost.",
        },
    )
    assert configured.status_code == 303
    return dataset_id


def _generate_evidence(
    app: Flask,
    client: FlaskClient,
    dataset_id: str,
) -> tuple[str, str]:
    generated = client.post(f"/insights/{dataset_id}")
    assert generated.status_code == 303
    evidence_path = (
        Path(app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json"
    )
    configuration_path = (
        Path(app.config["CONFIGURATION_DIR"]) / f"{dataset_id}.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    configuration = json.loads(
        configuration_path.read_text(encoding="utf-8")
    )
    return (
        configuration["primary_metric_id"],
        evidence["records"][0]["id"],
    )


def _save_manual_visualization(
    client: FlaskClient,
    dataset_id: str,
    metric_id: str,
) -> str:
    preview = client.post(
        f"/visualizations/{dataset_id}/preview",
        data={
            "title": "Revenue by segment",
            "purpose": "Which segment contributes the most revenue?",
            "chart_type": "category_bar",
            "measure_selectors": [f"metric:{metric_id}"],
            "x_column": "segment",
            "series_column": "",
            "aggregation": "sum",
            "date_granularity": "month",
            "filter_column": "",
            "filter_mode": "include",
            "filter_values": "",
            "date_start": "",
            "date_end": "",
            "sort_by": "value",
            "sort_direction": "descending",
            "top_n": "10",
            "scale": "linear",
            "bin_count": "10",
            "include_in_report": "yes",
        },
    )
    assert preview.status_code == 303
    token = preview.headers["Location"].rsplit("/", 1)[-1]
    saved = client.post(
        f"/visualizations/{dataset_id}/preview/{token}/save"
    )
    assert saved.status_code == 303
    return saved.headers["Location"].rsplit("/", 1)[-1]


def test_report_configuration_selects_and_escapes_current_assets(
    app: Flask,
    client: FlaskClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    dataset_id = _upload_and_configure(client)
    metric_id, evidence_id = _generate_evidence(app, client, dataset_id)
    visualization_id = _save_manual_visualization(
        client,
        dataset_id,
        metric_id,
    )

    def forbidden(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("Milestone 5A must not call Ollama")

    monkeypatch.setattr(
        "insight_reporter.routes.generate_configuration_suggestions",
        forbidden,
    )
    form = client.get(f"/reports/{dataset_id}/configure")
    assert form.status_code == 200
    assert b"Milestone 5A selects trusted report content only" in form.data
    assert metric_id.encode("ascii") in form.data
    assert evidence_id.encode("ascii") in form.data
    assert visualization_id.encode("ascii") in form.data
    assert b"/static/report_configuration.js" in form.data
    assert (
        f'data-report-kpi="{metric_id}"'.encode("ascii")
        in form.data
    )
    assert (
        f'data-report-evidence-metric="{metric_id}"'.encode("ascii")
        in form.data
    )
    assert (
        f'data-report-visualization-kpis="{metric_id}"'.encode("ascii")
        in form.data
    )
    assert b"Requires report KPI(s):" in form.data
    assert b"revenue" in form.data
    assert b"dataset-wide" in form.data
    script = client.get("/static/report_configuration.js")
    assert script.status_code == 200
    assert b"synchronizeEvidence" in script.data
    assert b"reportEvidenceRecommended" in script.data
    assert b"evidenceInput.checked = true" in script.data
    assert b"selectVisualizationKpis" in script.data
    assert b"visualizationInput.checked = false" in script.data

    saved = client.post(
        f"/reports/{dataset_id}/configure",
        data={
            "title": "<script>Quarterly report</script>",
            "business_objective": "Review trusted performance evidence.",
            "audience": "management",
            "tone": "professional",
            "detail_level": "standard",
            "user_notes": "<img src=x onerror=alert(1)>",
            "include_evidence_appendix": "yes",
            "selected_metric_ids": [metric_id],
            "selected_evidence_ids": [evidence_id],
            "selected_visualization_ids": [visualization_id],
        },
    )
    assert saved.status_code == 303
    assert saved.headers["Location"].endswith(
        f"/reports/{dataset_id}/configuration"
    )
    review = client.get(saved.headers["Location"])
    assert review.status_code == 200
    assert b"&lt;script&gt;Quarterly report&lt;/script&gt;" in review.data
    assert b"<script>Quarterly report</script>" not in review.data
    assert b"&lt;img src=x onerror=alert(1)&gt;" in review.data
    assert evidence_id.encode("ascii") in review.data
    assert visualization_id.encode("ascii") in review.data
    assert b"Report-generation readiness" in review.data
    assert b"Which segment contributes the most revenue?" in review.data

    report_path = (
        Path(app.config["REPORT_CONFIGURATION_DIR"])
        / f"{dataset_id}.json"
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["selected_metric_ids"] == [metric_id]
    assert payload["selected_evidence_ids"] == [evidence_id]
    assert payload["selected_visualization_ids"] == [visualization_id]
    assert len(payload["business_configuration_sha256"]) == 64
    assert len(payload["evidence_sha256"]) == 64
    assert len(payload["visualization_sha256s"][visualization_id]) == 64

    package_path = (
        Path(app.config["REPORT_PACKAGE_DIR"]) / f"{dataset_id}.json"
    )
    package = json.loads(package_path.read_text(encoding="utf-8"))
    assert package["schema_version"] == 1
    assert package["model_input_policy"]["raw_dataset_rows_included"] is False
    assert package["model_input_policy"]["all_numbers_calculated_by"] == "python"
    assert (
        package["model_input_policy"][
            "verified_categorical_evidence_values_included"
        ]
        is True
    )
    assert package["deterministic_evidence"][0]["id"] == evidence_id
    assert package["deterministic_evidence"][0]["metric_id"] == "DATASET"
    assert isinstance(
        package["deterministic_evidence"][0]["observation"],
        dict,
    )
    manual_evidence = package["manual_visualization_evidence"][0]
    assert manual_evidence["id"].startswith("MVE-")
    assert manual_evidence["visualization_id"] == visualization_id
    assert manual_evidence["purpose"] == (
        "Which segment contributes the most revenue?"
    )
    assert all(
        "row_number" not in row
        for row in manual_evidence["supporting_data"]
    )

    package_response = client.get(f"/reports/{dataset_id}/package")
    assert package_response.status_code == 200
    assert package_response.json == package


def test_default_report_selection_is_bounded_and_excludes_diagnostics(
    app: Flask,
    client: FlaskClient,
) -> None:
    dataset_id = _upload_and_configure(client)
    _generate_evidence(app, client, dataset_id)
    evidence = json.loads(
        (
            Path(app.config["EVIDENCE_DIR"])
            / f"{dataset_id}.json"
        ).read_text(encoding="utf-8")
    )

    form = client.get(f"/reports/{dataset_id}/configure")
    tags = re.findall(
        rb'<input type="checkbox" name="selected_evidence_ids"[^>]*>',
        form.data,
    )
    checked_ids = {
        re.search(rb'value="([^"]+)"', tag).group(1).decode()
        for tag in tags
        if b"checked" in tag
    }
    diagnostic_types = {
        "missing_data_warning",
        "insufficient_data_warning",
        "analysis_skipped",
    }
    diagnostic_ids = {
        record["id"]
        for record in evidence["records"]
        if record["insight_type"] in diagnostic_types
    }
    selected_correlations = [
        record
        for record in evidence["records"]
        if record["id"] in checked_ids
        and record["insight_type"] == "numeric_correlation"
    ]

    assert form.status_code == 200
    assert checked_ids
    assert len(checked_ids) <= 10
    assert checked_ids.isdisjoint(diagnostic_ids)
    assert len(selected_correlations) <= 2
    assert b"Data-quality and analysis diagnostics" in form.data
    assert b"data-report-evidence-recommended=\"false\"" in form.data


def test_invalid_selection_uses_reloadable_get_and_does_not_save(
    app: Flask,
    client: FlaskClient,
) -> None:
    dataset_id = _upload_and_configure(client)
    metric_id, _evidence_id = _generate_evidence(app, client, dataset_id)

    response = client.post(
        f"/reports/{dataset_id}/configure",
        data={
            "title": "Attempted report",
            "business_objective": "Review performance.",
            "audience": "management",
            "tone": "professional",
            "detail_level": "standard",
            "user_notes": "",
            "selected_metric_ids": [metric_id],
            "selected_evidence_ids": ["EVD-NOT-AVAILABLE"],
        },
        follow_redirects=True,
    )

    assert response.status_code == 400
    assert b"Report configuration rejected" in response.data
    assert b"Attempted report" in response.data
    assert not (
        Path(app.config["REPORT_CONFIGURATION_DIR"])
        / f"{dataset_id}.json"
    ).exists()


def test_kpi_only_route_works_without_evidence_or_visualizations(
    app: Flask,
    client: FlaskClient,
) -> None:
    dataset_id = _upload_and_configure(client)
    configuration_path = (
        Path(app.config["CONFIGURATION_DIR"]) / f"{dataset_id}.json"
    )
    configuration = json.loads(
        configuration_path.read_text(encoding="utf-8")
    )

    form = client.get(f"/reports/{dataset_id}/configure")
    assert form.status_code == 200
    assert b"No deterministic evidence exists yet" in form.data
    assert b"No saved manual visualizations are available" in form.data

    response = client.post(
        f"/reports/{dataset_id}/configure",
        data={
            "title": "KPI definition report",
            "business_objective": "Document the configured KPI.",
            "audience": "general",
            "tone": "concise",
            "detail_level": "brief",
            "user_notes": "",
            "selected_metric_ids": [
                configuration["primary_metric_id"]
            ],
        },
    )
    assert response.status_code == 303
    review = client.get(response.headers["Location"])
    assert review.status_code == 200
    assert b"No deterministic evidence was selected" in review.data
    assert b"No manual visualization was selected" in review.data


def test_saved_report_route_rejects_regenerated_evidence(
    app: Flask,
    client: FlaskClient,
) -> None:
    dataset_id = _upload_and_configure(client)
    metric_id, evidence_id = _generate_evidence(app, client, dataset_id)
    saved = client.post(
        f"/reports/{dataset_id}/configure",
        data={
            "title": "Staleness test",
            "business_objective": "Review current evidence.",
            "audience": "analyst",
            "tone": "technical",
            "detail_level": "detailed",
            "user_notes": "",
            "selected_metric_ids": [metric_id],
            "selected_evidence_ids": [evidence_id],
        },
    )
    assert saved.status_code == 303

    evidence_path = (
        Path(app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["records"].reverse()
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    review = client.get(saved.headers["Location"])
    assert review.status_code == 422
    assert b"stale or has been modified" in review.data


def test_previous_evidence_schema_requires_regeneration(
    app: Flask,
    client: FlaskClient,
) -> None:
    dataset_id = _upload_and_configure(client)
    metric_id, evidence_id = _generate_evidence(app, client, dataset_id)
    evidence_path = Path(app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["schema_version"] = 1
    for record in evidence["records"]:
        record.pop("observation")
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    response = client.post(
        f"/reports/{dataset_id}/configure",
        data={
            "title": "Previous evidence",
            "business_objective": "Verify report readiness.",
            "audience": "analyst",
            "tone": "technical",
            "detail_level": "standard",
            "user_notes": "",
            "selected_metric_ids": [metric_id],
            "selected_evidence_ids": [evidence_id],
        },
        follow_redirects=True,
    )

    assert response.status_code == 400
    assert b"previous schema" in response.data
    assert b"Regenerate deterministic insights" in response.data
    assert not (
        Path(app.config["REPORT_CONFIGURATION_DIR"])
        / f"{dataset_id}.json"
    ).exists()


def test_generated_report_is_versioned_escaped_and_failure_safe(
    app: Flask,
    client: FlaskClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    dataset_id = _upload_and_configure(client)
    metric_id, evidence_id = _generate_evidence(app, client, dataset_id)
    saved_configuration = client.post(
        f"/reports/{dataset_id}/configure",
        data={
            "title": "<script>Trusted report</script>",
            "company_name": "Example Analytics",
            "report_author": "Local Analyst",
            "business_objective": "Review evidence safely.",
            "audience": "analyst",
            "tone": "technical",
            "detail_level": "detailed",
            "user_notes": "<img src=x onerror=alert(1)>",
            "include_evidence_appendix": "yes",
            "selected_metric_ids": [metric_id],
            "selected_evidence_ids": [evidence_id],
        },
    )
    assert saved_configuration.status_code == 303

    def fake_generate(package, **kwargs):  # type: ignore[no-untyped-def]
        return generate_narrated_report(
            package,
            model=kwargs["model"],
            host=kwargs["host"],
            timeout_seconds=kwargs["timeout_seconds"],
            client=_FakeNarrationClient(),
        )

    monkeypatch.setattr(
        "insight_reporter.routes.generate_narrated_report",
        fake_generate,
    )
    generated = client.post(f"/reports/{dataset_id}/generate")
    assert generated.status_code == 303
    assert re.search(
        rf"/reports/{dataset_id}/generated/RPT-[0-9A-F]{{16}}$",
        generated.headers["Location"],
    )
    page = client.get(generated.headers["Location"])
    assert page.status_code == 200
    assert b"What it may mean" in page.data
    assert b"Python-generated facts" in page.data
    assert b"How this referenced value was produced" in page.data
    assert b"Confidence:" in page.data
    assert b"not a prediction probability" in page.data
    assert b"AI-generated and Python-validated" in page.data
    assert b"Primary finding for" in page.data
    assert b"What happened:" in page.data
    assert b"Why it matters:" in page.data
    assert b"Recommended action:" in page.data
    assert b"AI generation diagnostics" in page.data
    assert b"&lt;script&gt;Trusted report&lt;/script&gt;" in page.data
    assert b"Example Analytics" in page.data
    assert b"Local Analyst" in page.data
    assert b"<script>Trusted report</script>" not in page.data
    json_response = client.get(f"{generated.headers['Location']}/json")
    assert json_response.status_code == 200
    assert json_response.json["schema_version"] == 8
    assert len(json_response.json["executive_summary"]) == 5
    assert json_response.json["executive_summary"][0]["business_implication"]
    assert json_response.json["executive_summary"][0]["recommended_action"]
    assert (
        json_response.json["generation_diagnostics"][
            "executive_summary_source"
        ]
        == "ollama"
    )
    assert json_response.json["version"] == 1
    assert json_response.json["items"][0]["evidence_id"] == evidence_id
    assert (
        json_response.json["stories"][0]["fact_references"][0][
            "resolved_by"
        ]
        == "python"
    )
    story_id = json_response.json["stories"][0]["story_id"]

    pdf_response = client.get(
        f"{generated.headers['Location']}/pdf"
    )
    assert pdf_response.status_code == 200
    assert pdf_response.mimetype == "application/pdf"
    assert pdf_response.data.startswith(b"%PDF-")
    assert "attachment" in pdf_response.headers["Content-Disposition"]

    published = client.post(
        f"{generated.headers['Location']}/presentation",
        data={
            "included_story_ids": [story_id],
            f"story_order_{story_id}": "1",
        },
    )
    assert published.status_code == 303
    published_json = client.get(
        f"{generated.headers['Location']}/json"
    )
    assert published_json.json["version"] == 2
    assert published_json.json["stories"][0]["included"] is True

    def fake_regenerate(  # type: ignore[no-untyped-def]
        report,
        package,
        **kwargs,
    ):
        return regenerate_generated_story(
            report,
            package,
            story_id=kwargs["story_id"],
            model=kwargs["model"],
            host=kwargs["host"],
            timeout_seconds=kwargs["timeout_seconds"],
            client=_FakeNarrationClient(),
        )

    monkeypatch.setattr(
        "insight_reporter.routes.regenerate_generated_story",
        fake_regenerate,
    )
    regenerated = client.post(
        f"{generated.headers['Location']}/stories/{story_id}/regenerate"
    )
    assert regenerated.status_code == 303
    regenerated_json = client.get(
        f"{generated.headers['Location']}/json"
    )
    assert regenerated_json.json["version"] == 3

    report_files = tuple(
        (
            Path(app.config["GENERATED_REPORT_DIR"])
            / dataset_id
        ).glob("V*.json")
    )
    assert len(report_files) == 3
    report_asset_directories = tuple(
        (
            Path(app.config["GENERATED_REPORT_ASSET_DIR"])
            / dataset_id
        ).glob("V*")
    )
    assert len(report_asset_directories) == 3
    report_id = json_response.json["report_id"]
    history = client.get(f"/reports/{dataset_id}/history")
    historical_url = (
        f"/reports/{dataset_id}/generated/{report_id}/versions/1"
    )
    historical_page = client.get(historical_url)
    historical_json = client.get(f"{historical_url}/json")
    historical_pdf = client.get(f"{historical_url}/pdf")

    assert history.status_code == 200
    assert b"3 immutable version(s)" in history.data
    assert b"Matches current report package" in history.data
    assert historical_page.status_code == 200
    assert b"read-only historical snapshot" in historical_page.data
    assert historical_json.status_code == 200
    assert historical_json.json["version"] == 1
    assert historical_pdf.status_code == 200
    assert historical_pdf.data.startswith(b"%PDF-")

    class InvalidNarrationClient(_FakeNarrationClient):
        def chat(self, **kwargs: object) -> object:
            response = super().chat(**kwargs)
            content = json.loads(response["message"]["content"])
            content["finding"] = "The model invented a result of 999."
            response["message"]["content"] = json.dumps(content)
            return response

    def zero_ai_generate(package, **kwargs):  # type: ignore[no-untyped-def]
        return generate_narrated_report(
            package,
            model=kwargs["model"],
            host=kwargs["host"],
            timeout_seconds=kwargs["timeout_seconds"],
            client=InvalidNarrationClient(),
        )

    monkeypatch.setattr(
        "insight_reporter.routes.generate_narrated_report",
        zero_ai_generate,
    )
    zero_ai = client.post(
        f"/reports/{dataset_id}/generate",
        follow_redirects=True,
    )
    assert zero_ai.status_code == 503
    assert b"none passed evidence validation" in zero_ai.data
    assert len(
        tuple(
            (
                Path(app.config["GENERATED_REPORT_DIR"])
                / dataset_id
            ).glob("V*.json")
        )
    ) == 3

    def unavailable(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ReportNarrationError("Ollama test failure.")

    monkeypatch.setattr(
        "insight_reporter.routes.generate_narrated_report",
        unavailable,
    )
    failed = client.post(
        f"/reports/{dataset_id}/generate",
        follow_redirects=True,
    )
    assert failed.status_code == 503
    assert b"Report generation unavailable" in failed.data
    assert b"Ollama test failure" in failed.data
    assert len(
        tuple(
            (
                Path(app.config["GENERATED_REPORT_DIR"])
                / dataset_id
            ).glob("V*.json")
        )
    ) == 3
    assert client.get(generated.headers["Location"]).status_code == 200

    renamed_report = client.post(
        f"/workspaces/{dataset_id}/reports/{report_id}/name",
        data={"name": "Management performance brief"},
    )
    workspace_page = client.get(f"/workspaces/{dataset_id}")
    archived_report = client.post(
        f"/workspaces/{dataset_id}/reports/{report_id}/archive"
    )

    assert renamed_report.status_code == 303
    assert b"Management performance brief" in workspace_page.data
    assert archived_report.status_code == 303
    assert client.get(generated.headers["Location"]).status_code == 404
    assert client.get(historical_url).status_code == 404
    assert b"Recoverably deleted report versions" in client.get(
        f"/reports/{dataset_id}/history"
    ).data

    restored_report = client.post(
        f"/workspaces/{dataset_id}/reports/{report_id}/restore"
    )
    archived_source = client.post(
        f"/workspaces/{dataset_id}/source/archive"
    )

    assert restored_report.status_code == 303
    assert archived_source.status_code == 303
    assert not tuple(Path(app.config["UPLOAD_DIR"]).glob(f"{dataset_id}.*"))
    assert client.get(generated.headers["Location"]).status_code == 404
    assert client.get(historical_url).status_code == 200
    assert client.get(f"{historical_url}/json").status_code == 200
    assert client.get(f"{historical_url}/pdf").status_code == 200
    assert b"Open saved report" in client.get(
        f"/workspaces/{dataset_id}"
    ).data

    restored_source = client.post(
        f"/workspaces/{dataset_id}/source/restore"
    )
    assert restored_source.status_code == 303
    assert (
        Path(app.config["UPLOAD_DIR"]) / f"{dataset_id}.csv"
    ).is_file()
