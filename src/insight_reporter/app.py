"""Flask application factory."""

from pathlib import Path

from flask import Flask

from insight_reporter.config import DefaultConfig
from insight_reporter.logging_config import configure_logging
from insight_reporter.routes import core

def create_app(test_config: dict[str, object] | None = None) -> Flask:
    """Create a configured application instance.

    A factory keeps tests isolated and prevents configuration from being read or
    mutated at import time.
    """

    app = Flask(
        __name__,
        instance_path=str(DefaultConfig.INSTANCE_PATH),
        instance_relative_config=True,
    )
    app.config.from_object(DefaultConfig)

    if test_config:
        app.config.update(test_config)

    app.config.setdefault("UPLOAD_DIR", Path(app.instance_path) / "uploads")
    app.config.setdefault("CONFIGURATION_DIR", Path(app.instance_path) / "configurations")
    app.config.setdefault("INSIGHT_DIR", Path(app.instance_path) / "insights")
    app.config.setdefault("EVIDENCE_DIR", Path(app.instance_path) / "evidence")
    app.config.setdefault("CHART_DIR", Path(app.instance_path) / "charts")
    app.config.setdefault("VISUALIZATION_DIR", Path(app.instance_path) / "visualizations")
    app.config.setdefault(
        "REPORT_CONFIGURATION_DIR",
        Path(app.instance_path) / "report_configurations",
    )
    app.config.setdefault(
        "REPORT_PACKAGE_DIR",
        Path(app.instance_path) / "report_packages",
    )
    app.config.setdefault(
        "GENERATED_REPORT_DIR",
        Path(app.instance_path) / "generated_reports",
    )
    app.config.setdefault(
        "VISUALIZATION_PREVIEW_DIR",
        Path(app.instance_path) / "visualization_previews",
    )
    app.config.setdefault("NAVIGATION_STATE_DIR", Path(app.instance_path) / "navigation_state")
    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["CONFIGURATION_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["INSIGHT_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["EVIDENCE_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["CHART_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["VISUALIZATION_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["REPORT_CONFIGURATION_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["REPORT_PACKAGE_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["GENERATED_REPORT_DIR"]).mkdir(
        parents=True,
        exist_ok=True,
    )
    Path(app.config["VISUALIZATION_PREVIEW_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["NAVIGATION_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

    configure_logging(app)
    app.register_blueprint(core)

    @app.after_request
    def add_security_headers(response):  # type: ignore[no-untyped-def]
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    return app
