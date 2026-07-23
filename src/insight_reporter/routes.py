"""Local health and secure multi-format dataset routes."""

import json
import re
from dataclasses import asdict
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import RequestEntityTooLarge

from insight_reporter.business_config import (
    BusinessConfiguration,
    BusinessConfigurationError,
    load_business_configuration,
    remove_metric,
    save_business_configuration,
    set_primary_metric,
    update_metric_settings,
    validate_business_configuration,
    validate_derived_business_configuration,
)
from insight_reporter.configuration_suggestions import (
    ConfigurationSuggestion,
    ConfigurationSuggestionError,
    generate_configuration_suggestions,
)
from insight_reporter.dataset_ingestion import (
    DatasetValidationError,
    find_dataset_path,
    ingest_dataset,
    load_xlsx_selection,
    save_xlsx_selection,
)
from insight_reporter.dataset_profile import (
    DatasetProfile,
    DatasetProfileError,
    profile_dataset,
)
from insight_reporter.dataset_view import (
    DatasetView,
    DatasetViewError,
    discover_xlsx_tables,
    load_dataset_view,
    source_id_from_hash,
)
from insight_reporter.derived_kpi_suggestions import (
    DerivedKpiSuggestion,
    DerivedKpiSuggestionError,
    generate_derived_kpi_suggestions,
)
from insight_reporter.derived_metrics import (
    DerivedMetric,
    DerivedMetricError,
    convert_legacy_metric_to_formula,
    preview_derived_metric,
    validate_derived_metric,
    validate_formula_metric,
)
from insight_reporter.insight_engine import (
    InsightEngineError,
    generate_insights,
    save_insight_report,
)
from insight_reporter.navigation_state import (
    NavigationStateError,
    load_navigation_state,
    save_navigation_state,
)

core = Blueprint("core", __name__)


def _upload_limits() -> dict[str, int]:
    return {
        "max_bytes": int(current_app.config["MAX_UPLOAD_BYTES"]),
        "max_rows": int(current_app.config["MAX_CSV_ROWS"]),
        "max_columns": int(current_app.config["MAX_CSV_COLUMNS"]),
    }


@core.get("/")
def upload_form():  # type: ignore[no-untyped-def]
    """Display the one-file dataset upload form."""

    state = _load_view_state("upload")
    return (
        render_template(
            "upload.html",
            error=state.get("error") if isinstance(state.get("error"), str) else None,
            **_upload_limits(),
        ),
        _state_status(state),
    )


@core.post("/upload")
def upload_dataset():  # type: ignore[no-untyped-def]
    """Validate, retain, and preview exactly one supported dataset file."""

    uploaded_files = [
        item
        for field_name in request.files
        for item in request.files.getlist(field_name)
    ]
    if len(uploaded_files) != 1 or "file" not in request.files:
        return _redirect_with_state(
            "core.upload_form",
            {
                "view": "upload",
                "error": "Select exactly one CSV, JSON, or XLSX file.",
                "status_code": 400,
            },
        )

    try:
        result = ingest_dataset(
            uploaded_files[0],
            upload_dir=Path(current_app.config["UPLOAD_DIR"]),
            max_bytes=int(current_app.config["MAX_UPLOAD_BYTES"]),
            max_rows=int(current_app.config["MAX_CSV_ROWS"]),
            max_columns=int(current_app.config["MAX_CSV_COLUMNS"]),
        )
    except DatasetValidationError as error:
        current_app.logger.warning("Dataset upload rejected: %s", error)
        return _redirect_with_state(
            "core.upload_form",
            {"view": "upload", "error": str(error), "status_code": error.status_code},
        )

    current_app.logger.info(
        "Dataset upload accepted: id=%s format=%s bytes=%d rows=%s columns=%s sha256=%s",
        result.internal_filename,
        result.source_format,
        result.size_bytes,
        result.row_count,
        result.column_count,
        result.sha256,
    )
    dataset_id = Path(result.internal_filename).stem
    if result.source_format == "xlsx":
        if result.requires_table_selection:
            return redirect(
                url_for("core.excel_sheet_selection", dataset_id=dataset_id),
                code=303,
            )
        save_xlsx_selection(
            Path(current_app.config["UPLOAD_DIR"]),
            dataset_id,
            result.table_names[0],
        )
    try:
        view = _load_dataset_view_for_id(dataset_id)
        profile_dataset(
            view,
            size_bytes=result.size_bytes,
            preview_rows=int(current_app.config["CSV_PREVIEW_ROWS"]),
        )
    except (DatasetProfileError, DatasetValidationError, DatasetViewError) as error:
        current_app.logger.error(
            "Accepted dataset could not be profiled: id=%s", dataset_id
        )
        return _redirect_with_state(
            "core.upload_form",
            {"view": "upload", "error": str(error), "status_code": 422},
        )

    return redirect(url_for("core.dataset_profile", dataset_id=dataset_id), code=303)


@core.get("/dataset/<dataset_id>")
def dataset_profile(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display a stable GET page for an uploaded dataset and transient UI results."""

    dataset_path = _dataset_path(dataset_id)
    if dataset_path.suffix == ".xlsx" and load_xlsx_selection(
        Path(current_app.config["UPLOAD_DIR"]), dataset_id
    ) is None:
        return redirect(
            url_for("core.excel_sheet_selection", dataset_id=dataset_id), code=303
        )
    profile = _load_profile(dataset_id)
    state = _load_view_state("profile", dataset_id=dataset_id)
    return _render_profile(
        dataset_id,
        profile,
        configuration_error=_state_text(state, "configuration_error"),
        form_data=_form_from_state(state.get("form_data")),
        suggestions=_state_list(state, "suggestions"),
        suggestion_error=_state_text(state, "suggestion_error"),
        rejected_suggestion_count=_state_count(state, "rejected_suggestion_count"),
        derived_suggestions=_state_list(state, "derived_suggestions"),
        derived_suggestion_error=_state_text(state, "derived_suggestion_error"),
        rejected_derived_suggestion_count=_state_count(
            state, "rejected_derived_suggestion_count"
        ),
        review_notice=_state_text(state, "review_notice"),
        status_code=_state_status(state),
    )


@core.get("/dataset/<dataset_id>/sheet")
def excel_sheet_selection(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display explicit worksheet selection for a retained XLSX workbook."""

    path = _dataset_path(dataset_id)
    if path.suffix != ".xlsx":
        abort(404)
    try:
        table_names = discover_xlsx_tables(path)
    except DatasetViewError as error:
        abort(422, description=str(error))
    state = _load_view_state("sheet", dataset_id=dataset_id)
    return (
        render_template(
            "sheet_selection.html",
            dataset_id=dataset_id,
            table_names=table_names,
            error=_state_text(state, "error"),
        ),
        _state_status(state),
    )


@core.post("/dataset/<dataset_id>/sheet")
def select_excel_sheet(dataset_id: str):  # type: ignore[no-untyped-def]
    """Validate and retain one user-selected Excel worksheet."""

    path = _dataset_path(dataset_id)
    if path.suffix != ".xlsx":
        abort(404)
    table_name = request.form.get("table_name", "")
    try:
        available = discover_xlsx_tables(path)
        if table_name not in available:
            raise DatasetValidationError("Select an available Excel worksheet.")
        view = load_dataset_view(
            path,
            table_name=table_name,
            max_rows=int(current_app.config["MAX_CSV_ROWS"]),
            max_columns=int(current_app.config["MAX_CSV_COLUMNS"]),
        )
        profile_dataset(
            view,
            size_bytes=path.stat().st_size,
            preview_rows=int(current_app.config["CSV_PREVIEW_ROWS"]),
        )
        save_xlsx_selection(
            Path(current_app.config["UPLOAD_DIR"]), dataset_id, table_name
        )
    except (DatasetValidationError, DatasetViewError, DatasetProfileError) as error:
        return _redirect_with_state(
            "core.excel_sheet_selection",
            {
                "view": "sheet",
                "dataset_id": dataset_id,
                "error": str(error),
                "status_code": 400,
            },
            dataset_id=dataset_id,
        )
    return redirect(url_for("core.dataset_profile", dataset_id=dataset_id), code=303)


@core.post("/suggest/<dataset_id>")
def suggest_configurations(dataset_id: str):  # type: ignore[no-untyped-def]
    """Generate advisory configurations from profile metadata using local Ollama."""

    profile = _load_profile(dataset_id)
    if not profile.kpi_candidates:
        return _redirect_profile_state(
            dataset_id,
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
        return _redirect_profile_state(
            dataset_id, suggestion_error=str(error), status_code=503
        )

    current_app.logger.info(
        "Local configuration suggestions generated: id=%s accepted=%d rejected=%d model=%s",
        dataset_id,
        len(batch.suggestions),
        batch.rejected_count,
        current_app.config["OLLAMA_MODEL"],
    )
    return _redirect_profile_state(
        dataset_id,
        suggestions=[asdict(suggestion) for suggestion in batch.suggestions],
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
        return _redirect_profile_state(
            dataset_id,
            configuration_error=str(error),
            form_data=_form_to_state(request.form),
            status_code=400,
        )

    return _redirect_profile_state(
        dataset_id,
        form_data=_form_to_state(request.form),
        review_notice="AI suggestion loaded. Review or edit every field before confirming.",
    )


@core.post("/suggest-derived/<dataset_id>")
def suggest_derived_kpis(dataset_id: str):  # type: ignore[no-untyped-def]
    """Generate optional restricted derived-KPI definitions using local Ollama."""

    profile = _load_profile(dataset_id)
    try:
        batch = generate_derived_kpi_suggestions(
            profile,
            model=str(current_app.config["OLLAMA_MODEL"]),
            host=str(current_app.config["OLLAMA_HOST"]),
            timeout_seconds=int(current_app.config["OLLAMA_TIMEOUT_SECONDS"]),
        )
    except DerivedKpiSuggestionError as error:
        current_app.logger.warning(
            "Derived KPI suggestions unavailable: id=%s model=%s reason=%s",
            dataset_id,
            current_app.config["OLLAMA_MODEL"],
            error,
        )
        return _redirect_profile_state(
            dataset_id, derived_suggestion_error=str(error), status_code=503
        )

    return _redirect_profile_state(
        dataset_id,
        derived_suggestions=[
            _derived_suggestion_view(suggestion) for suggestion in batch.suggestions
        ],
        rejected_derived_suggestion_count=batch.rejected_count,
    )


@core.post("/review-derived/<dataset_id>")
def review_derived_kpi(dataset_id: str):  # type: ignore[no-untyped-def]
    """Redirect an AI-suggested or user-edited formula to a stable GET preview."""

    _load_profile(dataset_id)
    return _redirect_with_state(
        "core.derived_kpi_editor",
        {
            "view": "derived",
            "dataset_id": dataset_id,
            "form_data": _form_to_state(request.form),
            "status_code": 200,
        },
        dataset_id=dataset_id,
    )


@core.get("/derived/<dataset_id>")
def derived_kpi_editor(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display a stable, reproducible derived-KPI editor and Python preview."""

    profile = _load_profile(dataset_id)
    state = _load_view_state("derived", dataset_id=dataset_id)
    form_data = _form_from_state(state.get("form_data"))
    if form_data is None:
        return redirect(url_for("core.dataset_profile", dataset_id=dataset_id), code=303)
    configuration_error = _state_text(state, "configuration_error")
    try:
        metric = _derived_metric_from_values(profile, form_data)
        _populate_formula_fields(form_data, metric)
        existing = _load_existing_configuration(dataset_id, profile)
        if existing is not None:
            _populate_existing_business_fields(form_data, existing)
        preview = preview_derived_metric(
            _load_dataset_view_for_id(dataset_id),
            metric,
        )
    except DerivedMetricError as error:
        return _render_derived_editor(
            dataset_id,
            profile,
            metric=None,
            preview=None,
            form_data=form_data,
            configuration_error=configuration_error or str(error),
            status_code=(
                _state_status(state, default=400) if configuration_error else 400
            ),
        )

    if (
        not form_data.get("target_or_benchmark", "").strip()
        and form_data.get("benchmark_strategy") == "dataset_mean"
        and preview.mean is not None
    ):
        form_data["target_or_benchmark"] = f"{preview.mean:.10g}"

    return _render_derived_editor(
        dataset_id,
        profile,
        metric=metric,
        preview=preview,
        configuration_error=configuration_error,
        form_data=form_data,
        status_code=_state_status(state),
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
            secondary_kpis=request.form.getlist("secondary_kpis"),
            existing_configuration=_load_existing_configuration(dataset_id, profile),
        )
    except BusinessConfigurationError as error:
        return _redirect_profile_state(
            dataset_id,
            configuration_error=str(error),
            form_data=_form_to_state(request.form),
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
    return redirect(
        url_for("core.saved_configuration", dataset_id=dataset_id), code=303
    )


@core.post("/configure-derived/<dataset_id>")
def configure_derived_kpi(dataset_id: str):  # type: ignore[no-untyped-def]
    """Confirm a restricted derived KPI and the remaining business selections."""

    profile = _load_profile(dataset_id)
    form_data = request.form.copy()
    try:
        metric = _derived_metric_from_values(profile, form_data)
        preview_derived_metric(
            _load_dataset_view_for_id(dataset_id),
            metric,
        )
    except DerivedMetricError as error:
        return _redirect_with_state(
            "core.derived_kpi_editor",
            {
                "view": "derived",
                "dataset_id": dataset_id,
                "form_data": _form_to_state(form_data),
                "configuration_error": str(error),
                "status_code": 400,
            },
            dataset_id=dataset_id,
        )

    try:
        configuration = validate_derived_business_configuration(
            profile,
            dataset_id=dataset_id,
            derived_metric=metric,
            kpi_direction=request.form.get("kpi_direction", ""),
            date_column=request.form.get("date_column", ""),
            category_columns=request.form.getlist("category_columns"),
            target_or_benchmark=request.form.get("target_or_benchmark", ""),
            business_objective=request.form.get("business_objective", ""),
            existing_configuration=_load_existing_configuration(dataset_id, profile),
            metric_role=request.form.get("metric_role", "primary"),
        )
    except BusinessConfigurationError as error:
        return _redirect_with_state(
            "core.derived_kpi_editor",
            {
                "view": "derived",
                "dataset_id": dataset_id,
                "form_data": _form_to_state(form_data),
                "configuration_error": str(error),
                "status_code": 400,
            },
            dataset_id=dataset_id,
        )

    configuration_path = save_business_configuration(
        configuration,
        configuration_dir=Path(current_app.config["CONFIGURATION_DIR"]),
    )
    current_app.logger.info(
        "Derived business configuration saved: id=%s path=%s metric=%s",
        dataset_id,
        configuration_path.name,
        metric.name,
    )
    return redirect(
        url_for("core.saved_configuration", dataset_id=dataset_id), code=303
    )


@core.get("/configuration/<dataset_id>")
def saved_configuration(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display a retained configuration from a stable GET URL."""

    profile = _load_profile(dataset_id)
    configuration_path = Path(current_app.config["CONFIGURATION_DIR"]) / (
        f"{dataset_id}.json"
    )
    if not configuration_path.is_file():
        abort(404)
    try:
        configuration = load_business_configuration(configuration_path, profile=profile)
    except BusinessConfigurationError as error:
        abort(422, description=str(error))
    state = _load_view_state("configuration", dataset_id=dataset_id)
    return _render_saved_configuration(
        configuration,
        configuration_error=_state_text(state, "configuration_error"),
        status_code=_state_status(state),
    )


@core.post("/configuration/<dataset_id>/primary")
def choose_primary_metric(dataset_id: str):  # type: ignore[no-untyped-def]
    """Select one existing KPI as primary without rebuilding the registry."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
        configuration = set_primary_metric(
            configuration, request.form.get("metric_id", "")
        )
        save_business_configuration(
            configuration,
            configuration_dir=Path(current_app.config["CONFIGURATION_DIR"]),
        )
    except BusinessConfigurationError as error:
        return _redirect_configuration_error(dataset_id, str(error))
    return redirect(
        url_for("core.saved_configuration", dataset_id=dataset_id), code=303
    )


@core.post("/configuration/<dataset_id>/metric")
def edit_metric_settings(dataset_id: str):  # type: ignore[no-untyped-def]
    """Edit the direction and optional benchmark of one configured KPI."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
        configuration = update_metric_settings(
            configuration,
            request.form.get("metric_id", ""),
            kpi_direction=request.form.get("kpi_direction", ""),
            target_or_benchmark=request.form.get("target_or_benchmark", ""),
        )
        save_business_configuration(
            configuration,
            configuration_dir=Path(current_app.config["CONFIGURATION_DIR"]),
        )
    except BusinessConfigurationError as error:
        return _redirect_configuration_error(dataset_id, str(error))
    return redirect(
        url_for("core.saved_configuration", dataset_id=dataset_id), code=303
    )


@core.post("/configuration/<dataset_id>/remove")
def remove_configured_metric(dataset_id: str):  # type: ignore[no-untyped-def]
    """Remove a non-primary KPI from the registry."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
        configuration = remove_metric(
            configuration, request.form.get("metric_id", "")
        )
        save_business_configuration(
            configuration,
            configuration_dir=Path(current_app.config["CONFIGURATION_DIR"]),
        )
    except BusinessConfigurationError as error:
        return _redirect_configuration_error(dataset_id, str(error))
    return redirect(
        url_for("core.saved_configuration", dataset_id=dataset_id), code=303
    )


@core.post("/insights/<dataset_id>")
def deterministic_insights(dataset_id: str):  # type: ignore[no-untyped-def]
    """Generate and save factual observations using Python only."""

    profile = _load_profile(dataset_id)
    configuration_path = Path(current_app.config["CONFIGURATION_DIR"]) / (
        f"{dataset_id}.json"
    )
    if not configuration_path.is_file():
        abort(404)

    try:
        configuration = load_business_configuration(configuration_path, profile=profile)
        report = generate_insights(
            _load_dataset_view_for_id(dataset_id),
            profile=profile,
            configuration=configuration,
        )
        report_path = save_insight_report(
            report,
            insight_dir=Path(current_app.config["INSIGHT_DIR"]),
        )
    except (BusinessConfigurationError, InsightEngineError) as error:
        current_app.logger.warning(
            "Deterministic insights rejected: id=%s reason=%s", dataset_id, error
        )
        abort(422, description=str(error))

    current_app.logger.info(
        "Deterministic insights generated: id=%s count=%d path=%s",
        dataset_id,
        len(report.insights),
        report_path.name,
    )
    return redirect(
        url_for("core.saved_insights", dataset_id=dataset_id), code=303
    )


@core.get("/insights/<dataset_id>")
def saved_insights(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display retained deterministic evidence from a stable GET URL."""

    _load_profile(dataset_id)
    report_path = Path(current_app.config["INSIGHT_DIR"]) / f"{dataset_id}.json"
    if not report_path.is_file():
        abort(404)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        abort(422, description="Saved insight report is unreadable.")
    if not isinstance(report, dict) or report.get("dataset_id") != dataset_id:
        abort(422, description="Saved insight report is invalid.")
    return render_template(
        "insights.html",
        report=report,
        report_json=json.dumps(report, indent=2, sort_keys=True),
    )


@core.app_errorhandler(RequestEntityTooLarge)
def request_too_large(_error: RequestEntityTooLarge):  # type: ignore[no-untyped-def]
    """Render a readable response when Flask rejects multipart size early."""

    return _redirect_with_state(
        "core.upload_form",
        {
            "view": "upload",
            "error": (
                "Upload request is too large. Dataset files are limited to "
                f"{_upload_limits()['max_bytes']} bytes."
            ),
            "status_code": 413,
        },
    )


def _dataset_path(dataset_id: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", dataset_id) is None:
        abort(404)
    try:
        path = find_dataset_path(
            Path(current_app.config["UPLOAD_DIR"]),
            dataset_id,
        )
    except DatasetValidationError as error:
        abort(422, description=str(error))
    if path is None:
        abort(404)
    return path


def _load_dataset_view_for_id(dataset_id: str) -> DatasetView:
    path = _dataset_path(dataset_id)
    table_name = (
        load_xlsx_selection(
            Path(current_app.config["UPLOAD_DIR"]),
            dataset_id,
        )
        if path.suffix == ".xlsx"
        else None
    )
    return load_dataset_view(
        path,
        table_name=table_name,
        max_rows=int(current_app.config["MAX_CSV_ROWS"]),
        max_columns=int(current_app.config["MAX_CSV_COLUMNS"]),
    )


def _load_profile(dataset_id: str) -> DatasetProfile:
    try:
        dataset_path = _dataset_path(dataset_id)
        view = _load_dataset_view_for_id(dataset_id)
        return profile_dataset(
            view,
            size_bytes=dataset_path.stat().st_size,
            preview_rows=int(current_app.config["CSV_PREVIEW_ROWS"]),
        )
    except (DatasetProfileError, DatasetValidationError, DatasetViewError, OSError) as error:
        current_app.logger.error(
            "Retained dataset could not be profiled: id=%s reason=%s",
            dataset_id,
            error,
        )
        abort(422, description=str(error))


def _render_profile(
    dataset_id: str,
    profile: DatasetProfile,
    *,
    configuration_error: str | None = None,
    form_data: object | None = None,
    suggestions: tuple[ConfigurationSuggestion, ...] = (),
    suggestion_error: str | None = None,
    rejected_suggestion_count: int = 0,
    derived_suggestions: tuple[DerivedKpiSuggestion, ...] = (),
    derived_suggestion_error: str | None = None,
    rejected_derived_suggestion_count: int = 0,
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
            derived_suggestions=derived_suggestions,
            derived_suggestion_error=derived_suggestion_error,
            rejected_derived_suggestion_count=rejected_derived_suggestion_count,
            derived_numeric_columns=tuple(
                column.name
                for column in profile.columns
                if column.inferred_type.value == "numeric"
            ),
            review_notice=review_notice,
            suggestion_model=current_app.config["OLLAMA_MODEL"],
        ),
        status_code,
    )


def _derived_metric_from_values(
    profile: DatasetProfile, values: MultiDict[str, str]
) -> DerivedMetric:
    formula = values.get("formula", "").strip()
    source_id = source_id_from_hash(
        profile.source_sha256,
        profile.source_table_name,
    )
    if formula:
        return validate_formula_metric(
            profile,
            name=values.get("name", ""),
            formula=formula,
            calculation_level=values.get("calculation_level", ""),
            aggregation=values.get("aggregation", ""),
            display_format=values.get("display_format", ""),
            source_id=source_id,
        )
    legacy = validate_derived_metric(
        profile,
        name=values.get("name", ""),
        operation=values.get("operation", ""),
        left_column=values.get("left_column", ""),
        right_column=values.get("right_column", ""),
        aggregation=values.get("aggregation", ""),
        display_format=values.get("display_format", ""),
    )
    return convert_legacy_metric_to_formula(
        profile,
        legacy,
        source_id=source_id,
    )


def _render_derived_editor(
    dataset_id: str,
    profile: DatasetProfile,
    *,
    metric: DerivedMetric | None,
    preview: object | None,
    form_data: object,
    configuration_error: str | None,
    status_code: int = 200,
):  # type: ignore[no-untyped-def]
    return (
        render_template(
            "derived_configuration.html",
            dataset_id=dataset_id,
            profile=profile,
            metric=metric,
            preview=preview,
            numeric_columns=tuple(
                column.name
                for column in profile.columns
                if column.inferred_type.value == "numeric"
            ),
            aggregation_options=("sum", "mean", "median", "min", "max", "formula"),
            display_format_options=("number", "percentage", "currency"),
            configuration_error=configuration_error,
            form_data=form_data,
        ),
        status_code,
    )


def _render_saved_configuration(
    configuration: BusinessConfiguration,
    *,
    configuration_error: str | None = None,
    status_code: int = 200,
):  # type: ignore[no-untyped-def]
    return (
        render_template(
            "configuration.html",
            configuration=configuration,
            configuration_error=configuration_error,
            configuration_json=json.dumps(
                configuration.to_dict(), indent=2, sort_keys=True
            ),
        ),
        status_code,
    )


def _configuration_path(dataset_id: str) -> Path:
    return Path(current_app.config["CONFIGURATION_DIR"]) / f"{dataset_id}.json"


def _load_existing_configuration(
    dataset_id: str, profile: DatasetProfile
) -> BusinessConfiguration | None:
    path = _configuration_path(dataset_id)
    if not path.is_file():
        return None
    return load_business_configuration(path, profile=profile)


def _require_configuration(
    dataset_id: str, profile: DatasetProfile
) -> BusinessConfiguration:
    configuration = _load_existing_configuration(dataset_id, profile)
    if configuration is None:
        raise BusinessConfigurationError("No saved business configuration exists.")
    return configuration


def _redirect_configuration_error(dataset_id: str, message: str):  # type: ignore[no-untyped-def]
    return _redirect_with_state(
        "core.saved_configuration",
        {
            "view": "configuration",
            "dataset_id": dataset_id,
            "configuration_error": message,
            "status_code": 400,
        },
        dataset_id=dataset_id,
    )


def _populate_formula_fields(
    form_data: MultiDict[str, str], metric: DerivedMetric
) -> None:
    form_data["formula"] = metric.formula_label
    form_data["calculation_level"] = metric.calculation_level
    form_data["aggregation"] = metric.aggregation


def _populate_existing_business_fields(
    form_data: MultiDict[str, str], configuration: BusinessConfiguration
) -> None:
    if not form_data.get("date_column", "") and configuration.date_column:
        form_data["date_column"] = configuration.date_column
    if not form_data.getlist("category_columns"):
        form_data.setlist("category_columns", list(configuration.category_columns))
    if not form_data.get("business_objective", ""):
        form_data["business_objective"] = configuration.business_objective
    if not form_data.get("metric_role", ""):
        form_data["metric_role"] = "secondary"


def _navigation_state_dir() -> Path:
    return Path(current_app.config["NAVIGATION_STATE_DIR"])


def _redirect_with_state(
    endpoint: str,
    payload: dict[str, object],
    *,
    dataset_id: str | None = None,
):  # type: ignore[no-untyped-def]
    token = save_navigation_state(payload, state_dir=_navigation_state_dir())
    route_values: dict[str, str] = {"state": token}
    if dataset_id is not None:
        route_values["dataset_id"] = dataset_id
    return redirect(url_for(endpoint, **route_values), code=303)


def _redirect_profile_state(
    dataset_id: str, *, status_code: int = 200, **values: object
):  # type: ignore[no-untyped-def]
    return _redirect_with_state(
        "core.dataset_profile",
        {
            "view": "profile",
            "dataset_id": dataset_id,
            "status_code": status_code,
            **values,
        },
        dataset_id=dataset_id,
    )


def _load_view_state(
    expected_view: str, *, dataset_id: str | None = None
) -> dict[str, object]:
    token = request.args.get("state", "")
    if not token:
        return {}
    try:
        payload = load_navigation_state(token, state_dir=_navigation_state_dir())
    except NavigationStateError as error:
        current_app.logger.info("Navigation state unavailable: %s", error)
        return {}
    if payload.get("view") != expected_view:
        return {}
    if dataset_id is not None and payload.get("dataset_id") != dataset_id:
        return {}
    return payload


def _state_status(state: dict[str, object], *, default: int = 200) -> int:
    value = state.get("status_code", default)
    if isinstance(value, bool) or not isinstance(value, int) or not 200 <= value <= 599:
        return default
    return value


def _state_text(state: dict[str, object], key: str) -> str | None:
    value = state.get(key)
    return value if isinstance(value, str) else None


def _state_count(state: dict[str, object], key: str) -> int:
    value = state.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _state_list(state: dict[str, object], key: str) -> list[object]:
    value = state.get(key)
    return value if isinstance(value, list) else []


def _form_to_state(values: MultiDict[str, str]) -> dict[str, list[str]]:
    return {key: list(items) for key, items in values.lists()}


def _form_from_state(value: object) -> MultiDict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    pairs: list[tuple[str, str]] = []
    for key, items in value.items():
        if not isinstance(key, str) or not isinstance(items, list) or not all(
            isinstance(item, str) for item in items
        ):
            return None
        pairs.extend((key, item) for item in items)
    return MultiDict(pairs)


def _derived_suggestion_view(suggestion: DerivedKpiSuggestion) -> dict[str, object]:
    metric = suggestion.metric
    return {
        "metric": {
            **metric.to_dict(),
            "formula_label": metric.formula_label,
            "source_columns": list(metric.source_columns),
        },
        "kpi_direction": suggestion.kpi_direction,
        "date_column": suggestion.date_column,
        "category_columns": list(suggestion.category_columns),
        "benchmark_strategy": suggestion.benchmark_strategy,
        "business_objective": suggestion.business_objective,
        "confidence": suggestion.confidence,
        "rationale": list(suggestion.rationale),
    }


@core.get("/health")
def health():  # type: ignore[no-untyped-def]
    """Return a non-sensitive process health signal."""

    return jsonify(service="ai-insight-reporter", status="ok")
