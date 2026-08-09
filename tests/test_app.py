"""Application-factory and health-route tests."""

from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient


def test_health_endpoint(client: FlaskClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "service": "ai-insight-reporter",
        "status": "ok",
    }


def test_root_starts_from_workspace_history(client: FlaskClient) -> None:
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"] == "/workspaces"

    workspaces = client.get("/workspaces")
    legacy_upload = client.get("/upload")

    assert workspaces.status_code == 200
    assert b"Create workspace" in workspaces.data
    assert legacy_upload.status_code == 200
    assert b"Upload one dataset" in legacy_upload.data


def test_workspace_scoped_error_keeps_workspace_recovery_navigation(
    client: FlaskClient,
) -> None:
    dataset_id = "a" * 32

    response = client.get(f"/workspaces/{dataset_id}")

    assert response.status_code == 404
    assert f'href="/workspaces/{dataset_id}"'.encode() in response.data
    assert b"Return to this workspace" in response.data
    assert b'href="/workspaces"' in response.data


def test_shared_ui_assets_are_self_hosted(client: FlaskClient) -> None:
    response = client.get("/workspaces")

    assert response.status_code == 200
    assert b'/static/vendor/bootstrap.min.css' in response.data
    assert b'/static/app.css' in response.data
    assert b'/static/vendor/bootstrap.bundle.min.js' in response.data
    assert b'/static/app.js' in response.data
    assert b'cdn.jsdelivr.net' not in response.data

    for asset_path in (
        "/static/vendor/bootstrap.min.css",
        "/static/vendor/bootstrap.bundle.min.js",
        "/static/app.css",
        "/static/app.js",
        "/static/visualization_builder.css",
        "/static/manual_visualization_builder.css",
        "/static/manual_visualization.css",
        "/static/generated_report.css",
    ):
        asset = client.get(asset_path)
        assert asset.status_code == 200
        assert asset.data


def test_upload_directory_is_outside_static_directory(app: Flask) -> None:
    upload_dir = Path(app.config["UPLOAD_DIR"]).resolve()
    static_dir = Path(app.static_folder or "").resolve()

    assert not upload_dir.is_relative_to(static_dir)


def test_configuration_directory_is_outside_static_directory(app: Flask) -> None:
    configuration_dir = Path(app.config["CONFIGURATION_DIR"]).resolve()
    static_dir = Path(app.static_folder or "").resolve()

    assert not configuration_dir.is_relative_to(static_dir)


def test_insight_directory_is_outside_static_directory(app: Flask) -> None:
    insight_dir = Path(app.config["INSIGHT_DIR"]).resolve()
    static_dir = Path(app.static_folder or "").resolve()

    assert not insight_dir.is_relative_to(static_dir)


def test_evidence_and_chart_directories_are_outside_static_directory(
    app: Flask,
) -> None:
    static_dir = Path(app.static_folder).resolve()

    for setting in (
        "EVIDENCE_DIR",
        "CHART_DIR",
        "VISUALIZATION_DIR",
        "VISUALIZATION_INSIGHT_DIR",
        "VISUALIZATION_PREVIEW_DIR",
        "REPORT_CONFIGURATION_DIR",
        "REPORT_PACKAGE_DIR",
        "GENERATED_REPORT_DIR",
        "GENERATED_REPORT_ASSET_DIR",
        "MODEL_RUN_METRICS_DIR",
        "WORKSPACE_DIR",
        "TRASH_DIR",
    ):
        directory = Path(app.config[setting]).resolve()
        assert directory.is_dir()
        assert not directory.is_relative_to(static_dir)


def test_navigation_state_directory_is_outside_static_directory(app: Flask) -> None:
    state_dir = Path(app.config["NAVIGATION_STATE_DIR"]).resolve()
    static_dir = Path(app.static_folder or "").resolve()

    assert not state_dir.is_relative_to(static_dir)


def test_health_endpoint_has_security_headers(client: FlaskClient) -> None:
    response = client.get("/health")

    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Content-Security-Policy"] == "default-src 'self'"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_untrusted_host_is_rejected(client: FlaskClient) -> None:
    response = client.get("/health", headers={"Host": "attacker.example"})

    assert response.status_code == 400


def test_browser_errors_use_the_shared_local_ui(client: FlaskClient) -> None:
    response = client.get("/this-page-does-not-exist")

    assert response.status_code == 404
    assert b"That page isn&#39;t available" in response.data
    assert b"app-error-state" in response.data
    assert b"app-navbar" in response.data
    assert b"Return to workspaces" in response.data
    assert b"No workspace data was changed" in response.data
