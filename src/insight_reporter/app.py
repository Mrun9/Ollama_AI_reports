"""Flask application factory."""

from pathlib import Path

from flask import Flask

from insight_reporter.config import DefaultConfig
from insight_reporter.logging_config import configure_logging
from insight_reporter.routes import core

_ARTIFACT_DIRECTORIES = (
    ("UPLOAD_DIR", "uploads"),
    ("WORKSPACE_DIR", "workspaces"),
    ("CONFIGURATION_DIR", "configurations"),
    ("INSIGHT_DIR", "insights"),
    ("EVIDENCE_DIR", "evidence"),
    ("CHART_DIR", "charts"),
    ("VISUALIZATION_DIR", "visualizations"),
    ("REPORT_CONFIGURATION_DIR", "report_configurations"),
    ("REPORT_PACKAGE_DIR", "report_packages"),
    ("GENERATED_REPORT_DIR", "generated_reports"),
    ("GENERATED_REPORT_ASSET_DIR", "generated_report_assets"),
    ("VISUALIZATION_PREVIEW_DIR", "visualization_previews"),
    ("NAVIGATION_STATE_DIR", "navigation_state"),
    ("TRASH_DIR", "trash"),
)


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

    # Runtime artifacts share one layout in production while tests can
    # override any directory independently.
    for setting, directory_name in _ARTIFACT_DIRECTORIES:
        app.config.setdefault(
            setting,
            Path(app.instance_path) / directory_name,
        )
        Path(app.config[setting]).mkdir(parents=True, exist_ok=True)

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
