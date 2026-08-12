"""POST/Redirect/GET regression tests for reliable browser history."""

import io
import re

from flask.testing import FlaskClient

from insight_reporter.configuration_suggestions import (
    ConfigurationSuggestion,
    SuggestionBatch,
)


def _upload_redirect(client: FlaskClient) -> tuple[str, str]:
    response = client.post(
        "/upload",
        data={
            "file": (
                io.BytesIO(
                    b"date,region,revenue,cost\n"
                    b"2026-01-01,North,100,60\n"
                    b"2026-01-02,South,120,70\n"
                    b"2026-01-03,North,140,80\n"
                ),
                "sales.csv",
                "text/csv",
            )
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 303
    location = response.headers["Location"]
    match = re.fullmatch(r"/dataset/([0-9a-f]{32})", location)
    assert match is not None
    return match.group(1), location


def _assert_get_is_reloadable(client: FlaskClient, location: str, text: bytes) -> None:
    first = client.get(location)
    second = client.get(location)

    assert first.status_code == 200
    assert second.status_code == 200
    assert text in first.data
    assert text in second.data


def test_profile_configuration_and_insight_pages_use_reloadable_get_urls(
    client: FlaskClient,
) -> None:
    dataset_id, profile_location = _upload_redirect(client)
    _assert_get_is_reloadable(client, profile_location, b"Dataset profile")

    configured = client.post(
        f"/configure/{dataset_id}",
        data={
            "primary_kpi": "revenue",
            "kpi_direction": "higher",
            "date_column": "date",
            "category_columns": ["region"],
            "target_or_benchmark": "100",
            "business_objective": "Evaluate revenue by region.",
        },
    )
    assert configured.status_code == 303
    assert configured.headers["Location"] == f"/configuration/{dataset_id}"
    _assert_get_is_reloadable(
        client, configured.headers["Location"], b"Business configuration saved"
    )

    insights = client.post(f"/insights/{dataset_id}")
    assert insights.status_code == 303
    assert insights.headers["Location"] == f"/insights/{dataset_id}"
    _assert_get_is_reloadable(
        client, insights.headers["Location"], b"Deterministic insights generated"
    )


def test_project_navigation_is_available_across_workspace_workflow_pages(
    client: FlaskClient,
) -> None:
    dataset_id, profile_location = _upload_redirect(client)
    configured = client.post(
        f"/configure/{dataset_id}",
        data={
            "primary_kpi": "revenue",
            "kpi_direction": "higher",
            "date_column": "date",
            "category_columns": ["region"],
            "target_or_benchmark": "100",
            "business_objective": "Evaluate revenue by region.",
        },
    )
    assert configured.status_code == 303

    expected_links = (
        f'href="/workspaces/{dataset_id}"'.encode(),
        f'href="/dataset/{dataset_id}"'.encode(),
        f'href="/visualizations/{dataset_id}"'.encode(),
        f'href="/reports/{dataset_id}/history"'.encode(),
    )
    pages = (
        client.get(f"/workspaces/{dataset_id}"),
        client.get(profile_location),
        client.get(configured.headers["Location"]),
        client.get(f"/workspaces/{dataset_id}/dashboard"),
        client.get(f"/reports/{dataset_id}/history"),
    )

    for page in pages:
        assert page.status_code == 200
        for link in expected_links:
            assert link in page.data

    assert b"app-navbar" in pages[0].data
    assert b"app-navbar" in pages[3].data
    assert b"Create first report" in pages[4].data
    assert f'href="/reports/{dataset_id}/configure"'.encode() in pages[4].data


def test_suggestion_and_derived_preview_state_survives_get_reload(
    client: FlaskClient, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    dataset_id, _location = _upload_redirect(client)
    suggestion = ConfigurationSuggestion(
        title="Revenue performance",
        primary_kpi="revenue",
        kpi_direction="higher",
        aggregation="sum",
        display_format="currency",
        date_column="date",
        category_columns=("region",),
        target_or_benchmark=None,
        target_scope="period",
        business_objective="Evaluate revenue by region.",
        confidence=0.9,
        rationale=("Revenue is measurable.",),
    )
    monkeypatch.setattr(
        "insight_reporter.routes.generate_configuration_suggestions",
        lambda *_args, **_kwargs: SuggestionBatch((suggestion,), 0),
    )

    suggested = client.post(f"/suggest/{dataset_id}")
    assert suggested.status_code == 303
    assert suggested.headers["Location"].startswith(f"/dataset/{dataset_id}?state=")
    _assert_get_is_reloadable(
        client, suggested.headers["Location"], b"Revenue performance"
    )

    derived = client.post(
        f"/review-derived/{dataset_id}",
        data={
            "name": "Profit",
            "operation": "subtract",
            "left_column": "revenue",
            "right_column": "cost",
            "aggregation": "sum",
            "display_format": "currency",
            "kpi_direction": "higher",
            "date_column": "date",
            "category_columns": ["region"],
            "benchmark_strategy": "dataset_mean",
            "target_or_benchmark": "",
            "business_objective": "Evaluate profit by region.",
        },
    )
    assert derived.status_code == 303
    assert derived.headers["Location"].startswith(f"/derived/{dataset_id}?state=")
    _assert_get_is_reloadable(
        client, derived.headers["Location"], b"Python calculation preview"
    )


def test_upload_error_redirect_is_reloadable(client: FlaskClient) -> None:
    response = client.post(
        "/upload",
        data={"file": (io.BytesIO(b""), "empty.csv", "text/csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 303
    assert response.headers["Location"].startswith("/upload?state=")
    first = client.get(response.headers["Location"])
    second = client.get(response.headers["Location"])
    assert first.status_code == 400
    assert second.status_code == 400
    assert b"Dataset is empty" in first.data
    assert b"Dataset is empty" in second.data
