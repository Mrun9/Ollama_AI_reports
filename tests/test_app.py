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


def test_root_displays_csv_upload_form(client: FlaskClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert b"Upload one CSV" in response.data
    assert b'method="post"' in response.data


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
