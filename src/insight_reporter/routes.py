"""Local health and secure CSV upload routes."""

import json
import re
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from insight_reporter.business_config import (
    BusinessConfigurationError,
    save_business_configuration,
    validate_business_configuration,
)
from insight_reporter.configuration_suggestions import (
    ConfigurationSuggestion,
    ConfigurationSuggestionError,
    generate_configuration_suggestions,
)
from insight_reporter.csv_ingestion import CSVValidationError, ingest_csv
from insight_reporter.dataset_profile import DatasetProfile, DatasetProfileError, profile_csv

core = Blueprint("core", __name__)


def _upload_limits() -> dict[str, int]:
    return {
        "max_bytes": int(current_app.config["MAX_UPLOAD_BYTES"]),
        "max_rows": int(current_app.config["MAX_CSV_ROWS"]),
        "max_columns": int(current_app.config["MAX_CSV_COLUMNS"]),
    }


@core.get("/")
def upload_form():  # type: ignore[no-untyped-def]
    """Display the one-file CSV upload form."""

    return render_template("upload.html", error=None, **_upload_limits())


@core.post("/upload")
def upload_csv():  # type: ignore[no-untyped-def]
    """Validate, retain, and preview exactly one CSV file."""

    uploaded_files = [
        item
        for field_name in request.files
        for item in request.files.getlist(field_name)
    ]
    if len(uploaded_files) != 1 or "file" not in request.files:
        return (
            render_template(
                "upload.html",
                error="Select exactly one CSV file.",
                **_upload_limits(),
            ),
            400,
        )

    try:
        result = ingest_csv(
            uploaded_files[0],
            upload_dir=Path(current_app.config["UPLOAD_DIR"]),
            max_bytes=int(current_app.config["MAX_UPLOAD_BYTES"]),
            max_rows=int(current_app.config["MAX_CSV_ROWS"]),
            max_columns=int(current_app.config["MAX_CSV_COLUMNS"]),
            preview_rows=int(current_app.config["CSV_PREVIEW_ROWS"]),
        )
    except CSVValidationError as error:
        current_app.logger.warning("CSV upload rejected: %s", error)
        return (
            render_template(
                "upload.html",
                error=str(error),
                **_upload_limits(),
            ),
            error.status_code,
        )

    current_app.logger.info(
        "CSV upload accepted: id=%s bytes=%d rows=%d columns=%d sha256=%s",
        result.internal_filename,
        result.size_bytes,
        result.row_count,
        result.column_count,
        result.sha256,
    )
    dataset_id = Path(result.internal_filename).stem
    try:
        profile = profile_csv(
            Path(current_app.config["UPLOAD_DIR"]) / result.internal_filename,
            preview_rows=int(current_app.config["CSV_PREVIEW_ROWS"]),
        )
    except DatasetProfileError as error:
        current_app.logger.error("Accepted CSV could not be profiled: id=%s", dataset_id)
        return (
            render_template(
                "upload.html",
                error=str(error),
                **_upload_limits(),
            ),
            422,
        )

    return _render_profile(dataset_id, profile)


@core.post("/suggest/<dataset_id>")
def suggest_configurations(dataset_id: str):  # type: ignore[no-untyped-def]
    """Generate advisory configurations from profile metadata using local Ollama."""

    profile = _load_profile(dataset_id)
    if not profile.kpi_candidates:
        return _render_profile(
            dataset_id,
            profile,
            suggestion_error="No measurable KPI candidates are available for suggestions.",
            status_code=400,
        )

    try:
        batch = generate_configuration_suggestions(
            profile,
            dataset_id=dataset_id,
            model=str(current_app.config["OLLAMA_MODEL"]),
            host=str(current_app.config["OLLAMA_HOST"]),
            timeout_seconds=int(current_app.config["OLLAMA_TIMEOUT_SECONDS"]),
        )
    except ConfigurationSuggestionError as error:
        current_app.logger.warning(
            "Local configuration suggestions unavailable: id=%s model=%s reason=%s",
            dataset_id,
            current_app.config["OLLAMA_MODEL"],
            error,
        )
        return _render_profile(
            dataset_id,
            profile,
            suggestion_error=str(error),
            status_code=503,
        )

    current_app.logger.info(
        "Local configuration suggestions generated: id=%s accepted=%d rejected=%d model=%s",
        dataset_id,
        len(batch.suggestions),
        batch.rejected_count,
        current_app.config["OLLAMA_MODEL"],
    )
    return _render_profile(
        dataset_id,
        profile,
        suggestions=batch.suggestions,
        rejected_suggestion_count=batch.rejected_count,
    )


@core.post("/review-suggestion/<dataset_id>")
def review_suggestion(dataset_id: str):  # type: ignore[no-untyped-def]
    """Revalidate an untrusted posted suggestion and prefill the manual form."""

    profile = _load_profile(dataset_id)
    try:
        validate_business_configuration(
            profile,
            dataset_id=dataset_id,
            primary_kpi=request.form.get("primary_kpi", ""),
            kpi_direction=request.form.get("kpi_direction", ""),
            date_column=request.form.get("date_column", ""),
            category_columns=request.form.getlist("category_columns"),
            target_or_benchmark=request.form.get("target_or_benchmark", ""),
            business_objective=request.form.get("business_objective", ""),
        )
    except BusinessConfigurationError as error:
        return _render_profile(
            dataset_id,
            profile,
            configuration_error=str(error),
            status_code=400,
        )

    return _render_profile(
        dataset_id,
        profile,
        form_data=request.form,
        review_notice="AI suggestion loaded. Review or edit every field before confirming.",
    )


@core.post("/configure/<dataset_id>")
def configure_dataset(dataset_id: str):  # type: ignore[no-untyped-def]
    """Validate and retain user-confirmed business selections."""

    profile = _load_profile(dataset_id)
    try:
        configuration = validate_business_configuration(
            profile,
            dataset_id=dataset_id,
            primary_kpi=request.form.get("primary_kpi", ""),
            kpi_direction=request.form.get("kpi_direction", ""),
            date_column=request.form.get("date_column", ""),
            category_columns=request.form.getlist("category_columns"),
            target_or_benchmark=request.form.get("target_or_benchmark", ""),
            business_objective=request.form.get("business_objective", ""),
        )
    except BusinessConfigurationError as error:
        return _render_profile(
            dataset_id,
            profile,
            configuration_error=str(error),
            form_data=request.form,
            status_code=400,
        )

    configuration_path = save_business_configuration(
        configuration,
        configuration_dir=Path(current_app.config["CONFIGURATION_DIR"]),
    )
    current_app.logger.info(
        "Business configuration saved: id=%s path=%s",
        dataset_id,
        configuration_path.name,
    )
    return render_template(
        "configuration.html",
        configuration=configuration,
        configuration_json=json.dumps(configuration.to_dict(), indent=2, sort_keys=True),
    )


@core.app_errorhandler(RequestEntityTooLarge)
def request_too_large(_error: RequestEntityTooLarge):  # type: ignore[no-untyped-def]
    """Render a readable response when Flask rejects multipart size early."""

    limits = _upload_limits()
    return (
        render_template(
            "upload.html",
            error=(
                "Upload request is too large. CSV files are limited to "
                f"{limits['max_bytes']} bytes."
            ),
            **limits,
        ),
        413,
    )


def _load_profile(dataset_id: str) -> DatasetProfile:
    if re.fullmatch(r"[0-9a-f]{32}", dataset_id) is None:
        abort(404)

    dataset_path = Path(current_app.config["UPLOAD_DIR"]) / f"{dataset_id}.csv"
    if not dataset_path.is_file():
        abort(404)

    try:
        return profile_csv(
            dataset_path,
            preview_rows=int(current_app.config["CSV_PREVIEW_ROWS"]),
        )
    except DatasetProfileError:
        current_app.logger.error("Retained CSV could not be profiled: id=%s", dataset_id)
        abort(422)


def _render_profile(
    dataset_id: str,
    profile: DatasetProfile,
    *,
    configuration_error: str | None = None,
    form_data: object | None = None,
    suggestions: tuple[ConfigurationSuggestion, ...] = (),
    suggestion_error: str | None = None,
    rejected_suggestion_count: int = 0,
    review_notice: str | None = None,
    status_code: int = 200,
):  # type: ignore[no-untyped-def]
    return (
        render_template(
            "preview.html",
            dataset_id=dataset_id,
            profile=profile,
            configuration_error=configuration_error,
            form_data=form_data,
            suggestions=suggestions,
            suggestion_error=suggestion_error,
            rejected_suggestion_count=rejected_suggestion_count,
            review_notice=review_notice,
            suggestion_model=current_app.config["OLLAMA_MODEL"],
        ),
        status_code,
    )


@core.get("/health")
def health():  # type: ignore[no-untyped-def]
    """Return a non-sensitive process health signal."""

    return jsonify(service="ai-insight-reporter", status="ok")
