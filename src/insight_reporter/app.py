"""Flask application factory."""

from pathlib import Path

from flask import Flask, render_template, request
from werkzeug.exceptions import HTTPException, SecurityError

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
    ("VISUALIZATION_INSIGHT_DIR", "visualization_insights"),
    ("REPORT_CONFIGURATION_DIR", "report_configurations"),
    ("REPORT_PACKAGE_DIR", "report_packages"),
    ("GENERATED_REPORT_DIR", "generated_reports"),
    ("GENERATED_REPORT_ASSET_DIR", "generated_report_assets"),
    ("VISUALIZATION_PREVIEW_DIR", "visualization_previews"),
    ("NAVIGATION_STATE_DIR", "navigation_state"),
    ("MODEL_RUN_METRICS_DIR", "model_run_metrics"),
    ("TRASH_DIR", "trash"),
)

_ERROR_TITLES = {
    400: "We couldn't use that request",
    404: "That page isn't available",
    405: "That action isn't available here",
    409: "That change conflicts with the current workspace",
    413: "That upload is too large",
    422: "That data couldn't be processed",
    500: "Something went wrong",
    503: "A local service is unavailable",
}


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

    @app.errorhandler(HTTPException)
    def render_http_error(error: HTTPException):  # type: ignore[no-untyped-def]
        """Render safe browser errors in the shared local UI."""

        # Host-header failures happen before normal URL building is safe.
        if isinstance(error, SecurityError):
            return error.get_response()
        status_code = error.code or 500
        candidate_dataset_id = (request.view_args or {}).get("dataset_id")
        dataset_id = (
            candidate_dataset_id
            if isinstance(candidate_dataset_id, str)
            else None
        )
        return (
            render_template(
                "error.html",
                error_code=status_code,
                error_title=_ERROR_TITLES.get(status_code, error.name),
                error_description=error.description,
                dataset_id=dataset_id,
            ),
            status_code,
        )

    @app.after_request
    def add_security_headers(response):  # type: ignore[no-untyped-def]
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    return app
