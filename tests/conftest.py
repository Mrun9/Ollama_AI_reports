"""Shared test fixtures."""

from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from insight_reporter import create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-only-secret-key",
            "UPLOAD_DIR": tmp_path / "uploads",
            "CONFIGURATION_DIR": tmp_path / "configurations",
            "INSIGHT_DIR": tmp_path / "insights",
            "EVIDENCE_DIR": tmp_path / "evidence",
            "CHART_DIR": tmp_path / "charts",
            "VISUALIZATION_DIR": tmp_path / "visualizations",
            "VISUALIZATION_PREVIEW_DIR": tmp_path / "visualization_previews",
            "NAVIGATION_STATE_DIR": tmp_path / "navigation_state",
        }
    )


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()
