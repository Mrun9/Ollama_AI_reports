"""Local health and secure multi-format dataset routes."""

import io
import json
import re
from dataclasses import asdict, replace
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import RequestEntityTooLarge

from insight_reporter.business_config import (
    BusinessConfiguration,
    BusinessConfigurationError,
    add_source_metrics,
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
from insight_reporter.dataset_context import (
    DatasetContext,
    build_dataset_context,
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
from insight_reporter.evidence_layer import (
    EvidenceError,
    chart_filename_for,
    delete_chart_files,
    generate_evidence,
    load_evidence_payload,
    referenced_chart_filenames,
    save_evidence_report,
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
from insight_reporter.report_configuration import (
    AUDIENCES,
    DETAIL_LEVELS,
    TONES,
    ReportConfiguration,
    ReportConfigurationError,
    artifact_sha256,
    load_report_configuration,
    save_report_configuration,
    validate_report_configuration,
)
from insight_reporter.report_generation_package import (
    ReportGenerationPackage,
    ReportGenerationPackageError,
    build_report_generation_package,
    save_report_generation_package,
)
from insight_reporter.report_narration import (
    GeneratedReport,
    NarratedEvidence,
    NarrativeStory,
    ReportNarrationError,
    generate_narrated_report,
    included_report_stories,
    latest_generated_report,
    load_generated_report,
    publish_report_presentation,
    regenerate_generated_story,
    save_generated_report,
)
from insight_reporter.report_pdf import (
    ReportPdfError,
    build_report_pdf,
    report_pdf_filename,
)
from insight_reporter.visualization_builder import (
    AGGREGATIONS,
    CHART_TYPES,
    DATE_GRANULARITIES,
    FILTER_MODES,
    SCALES,
    VisualizationArtifact,
    VisualizationError,
    artifact_chart_filename,
    build_visualization,
    delete_chart,
    delete_preview,
    list_visualizations,
    load_preview,
    load_visualization,
    parse_visualization_spec,
    save_preview,
    save_visualization,
    spec_to_form,
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
        existing = _load_existing_configuration(dataset_id, profile)
        excluded_kpis = (
            tuple(metric.name for metric in existing.metrics)
            if existing is not None
            else ()
        )
        batch = generate_configuration_suggestions(
            profile,
            dataset_id=dataset_id,
            model=str(current_app.config["OLLAMA_MODEL"]),
            host=str(current_app.config["OLLAMA_HOST"]),
            timeout_seconds=int(current_app.config["OLLAMA_TIMEOUT_SECONDS"]),
            excluded_kpis=excluded_kpis,
        )
    except (BusinessConfigurationError, ConfigurationSuggestionError) as error:
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
        existing = _load_existing_configuration(dataset_id, profile)
        if existing is None:
            validate_business_configuration(
                profile,
                dataset_id=dataset_id,
                primary_kpi=request.form.get("primary_kpi", ""),
                kpi_direction=request.form.get("kpi_direction", ""),
                date_column=request.form.get("date_column", ""),
                category_columns=request.form.getlist("category_columns"),
                target_or_benchmark=request.form.get(
                    "target_or_benchmark", ""
                ),
                business_objective=request.form.get(
                    "business_objective", ""
                ),
            )
            form_data = request.form.copy()
            notice = (
                "AI suggestion loaded. Review or edit every field before "
                "confirming."
            )
        else:
            form_data = request.form.copy()
            suggested_kpi = request.form.get("primary_kpi", "")
            add_source_metrics(
                profile,
                dataset_id=dataset_id,
                source_columns=[suggested_kpi],
                kpi_direction=request.form.get("kpi_direction", ""),
                existing_configuration=existing,
                date_column=request.form.get("date_column", ""),
                category_columns=request.form.getlist("category_columns"),
                business_objective=request.form.get(
                    "business_objective", ""
                ),
            )
            form_data.pop("primary_kpi", None)
            form_data.setlist("source_kpis", [suggested_kpi])
            form_data["context_submitted"] = "yes"
            notice = (
                "Additional KPI suggestion loaded. Review the KPI and shared "
                "analysis context before adding it."
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
        form_data=_form_to_state(form_data),
        review_notice=notice,
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
        existing = _load_existing_configuration(dataset_id, profile)
        if existing is None:
            configuration = validate_business_configuration(
                profile,
                dataset_id=dataset_id,
                primary_kpi=request.form.get("primary_kpi", ""),
                kpi_direction=request.form.get("kpi_direction", ""),
                date_column=request.form.get("date_column", ""),
                category_columns=request.form.getlist("category_columns"),
                target_or_benchmark=request.form.get(
                    "target_or_benchmark", ""
                ),
                business_objective=request.form.get(
                    "business_objective", ""
                ),
                secondary_kpis=request.form.getlist("secondary_kpis"),
            )
        else:
            context_submitted = (
                request.form.get("context_submitted", "") == "yes"
            )
            configuration = add_source_metrics(
                profile,
                dataset_id=dataset_id,
                source_columns=request.form.getlist("source_kpis"),
                kpi_direction=request.form.get("kpi_direction", ""),
                existing_configuration=existing,
                date_column=(
                    request.form.get("date_column", "")
                    if context_submitted
                    else None
                ),
                category_columns=(
                    request.form.getlist("category_columns")
                    if context_submitted
                    else None
                ),
                business_objective=(
                    request.form.get("business_objective", "")
                    if context_submitted
                    else None
                ),
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


@core.get("/reports/<dataset_id>/configure")
def report_configuration_form(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display the deterministic Milestone 5A report selection form."""

    profile = _load_profile(dataset_id)
    try:
        configuration, evidence, visualizations = _report_assets(
            dataset_id,
            profile,
        )
    except (BusinessConfigurationError, EvidenceError, VisualizationError) as error:
        abort(422, description=str(error))
    state = _load_view_state("report_configuration", dataset_id=dataset_id)
    form_data = _form_from_state(state.get("form_data"))
    stale_notice = None
    if form_data is None:
        report_path = _report_configuration_path(dataset_id)
        if report_path.is_file():
            try:
                saved = load_report_configuration(
                    report_path,
                    configuration=configuration,
                    evidence_payload=evidence,
                    visualizations=visualizations,
                )
                form_data = _report_form_from_configuration(saved)
            except ReportConfigurationError as error:
                stale_notice = str(error)
        if form_data is None:
            form_data = _default_report_form(
                configuration,
                evidence_payload=evidence,
                visualizations=visualizations,
            )
    return (
        render_template(
            "report_configuration_form.html",
            dataset_id=dataset_id,
            configuration=configuration,
            evidence_records=_sorted_evidence_records(evidence),
            visualizations=visualizations,
            visualization_metric_requirements={
                artifact.visualization_id: tuple(
                    measure.selector.removeprefix("metric:")
                    for measure in artifact.measures
                    if measure.selector.startswith("metric:")
                )
                for artifact in visualizations
                if artifact.visualization_id is not None
            },
            metric_names={
                metric.metric_id: metric.name
                for metric in configuration.metrics
            },
            form_data=form_data,
            audiences=AUDIENCES,
            tones=TONES,
            detail_levels=DETAIL_LEVELS,
            report_error=_state_text(state, "report_error"),
            stale_notice=stale_notice,
        ),
        _state_status(state),
    )


@core.post("/reports/<dataset_id>/configure")
def configure_report(dataset_id: str):  # type: ignore[no-untyped-def]
    """Validate and atomically retain one report selection."""

    profile = _load_profile(dataset_id)
    try:
        configuration, evidence, visualizations = _report_assets(
            dataset_id,
            profile,
        )
        report = validate_report_configuration(
            configuration,
            evidence_payload=evidence,
            visualizations=visualizations,
            title=request.form.get("title", ""),
            company_name=request.form.get("company_name", ""),
            report_author=request.form.get("report_author", ""),
            business_objective=request.form.get(
                "business_objective", ""
            ),
            audience=request.form.get("audience", ""),
            tone=request.form.get("tone", ""),
            detail_level=request.form.get("detail_level", ""),
            user_notes=request.form.get("user_notes", ""),
            include_evidence_appendix=request.form.get(
                "include_evidence_appendix", ""
            ),
            selected_metric_ids=request.form.getlist(
                "selected_metric_ids"
            ),
            selected_evidence_ids=request.form.getlist(
                "selected_evidence_ids"
            ),
            selected_visualization_ids=request.form.getlist(
                "selected_visualization_ids"
            ),
        )
        package = build_report_generation_package(
            report,
            configuration=configuration,
            evidence_payload=evidence,
            visualizations=visualizations,
        )
        save_report_configuration(
            report,
            report_configuration_dir=Path(
                current_app.config["REPORT_CONFIGURATION_DIR"]
            ),
        )
        save_report_generation_package(
            package,
            package_dir=Path(current_app.config["REPORT_PACKAGE_DIR"]),
        )
    except (
        BusinessConfigurationError,
        EvidenceError,
        ReportConfigurationError,
        ReportGenerationPackageError,
        VisualizationError,
    ) as error:
        return _redirect_with_state(
            "core.report_configuration_form",
            {
                "view": "report_configuration",
                "dataset_id": dataset_id,
                "form_data": _form_to_state(request.form),
                "report_error": str(error),
                "status_code": 400,
            },
            dataset_id=dataset_id,
        )
    return redirect(
        url_for("core.saved_report_configuration", dataset_id=dataset_id),
        code=303,
    )


@core.get("/reports/<dataset_id>/configuration")
def saved_report_configuration(
    dataset_id: str,
):  # type: ignore[no-untyped-def]
    """Review a saved report selection from a stable, revalidated GET URL."""

    profile = _load_profile(dataset_id)
    path = _report_configuration_path(dataset_id)
    if not path.is_file():
        abort(404)
    try:
        configuration, evidence, visualizations = _report_assets(
            dataset_id,
            profile,
        )
        report = load_report_configuration(
            path,
            configuration=configuration,
            evidence_payload=evidence,
            visualizations=visualizations,
        )
        package = build_report_generation_package(
            report,
            configuration=configuration,
            evidence_payload=evidence,
            visualizations=visualizations,
        )
    except (
        BusinessConfigurationError,
        EvidenceError,
        ReportConfigurationError,
        ReportGenerationPackageError,
        VisualizationError,
    ) as error:
        abort(422, description=str(error))
    metric_by_id = {
        metric.metric_id: metric for metric in configuration.metrics
    }
    evidence_by_id = {
        str(record.get("id")): record
        for record in _sorted_evidence_records(evidence)
    }
    visualization_by_id = {
        artifact.visualization_id: artifact
        for artifact in visualizations
        if artifact.visualization_id is not None
    }
    state = _load_view_state(
        "saved_report_configuration",
        dataset_id=dataset_id,
    )
    try:
        latest_report = latest_generated_report(
            dataset_id,
            generated_report_dir=Path(
                current_app.config["GENERATED_REPORT_DIR"]
            ),
            expected_package_sha256=artifact_sha256(package.to_dict()),
        )
    except ReportNarrationError:
        latest_report = None
    return (
        render_template(
            "report_configuration.html",
            report=report,
            selected_metrics=tuple(
                metric_by_id[metric_id]
                for metric_id in report.selected_metric_ids
            ),
            selected_evidence=tuple(
                evidence_by_id[evidence_id]
                for evidence_id in report.selected_evidence_ids
            ),
            selected_visualizations=tuple(
                visualization_by_id[visualization_id]
                for visualization_id in report.selected_visualization_ids
            ),
            report_package=package,
            report_json=json.dumps(
                report.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            narration_error=_state_text(state, "narration_error"),
            narration_model=current_app.config["OLLAMA_MODEL"],
            latest_generated_report=latest_report,
        ),
        _state_status(state),
    )


@core.get("/reports/<dataset_id>/package")
def report_generation_package(
    dataset_id: str,
):  # type: ignore[no-untyped-def]
    """Return the current bounded report-synthesis input as JSON."""

    profile = _load_profile(dataset_id)
    path = _report_configuration_path(dataset_id)
    if not path.is_file():
        abort(404)
    try:
        configuration, evidence, visualizations = _report_assets(
            dataset_id,
            profile,
        )
        report = load_report_configuration(
            path,
            configuration=configuration,
            evidence_payload=evidence,
            visualizations=visualizations,
        )
        package = build_report_generation_package(
            report,
            configuration=configuration,
            evidence_payload=evidence,
            visualizations=visualizations,
        )
    except (
        BusinessConfigurationError,
        EvidenceError,
        ReportConfigurationError,
        ReportGenerationPackageError,
        VisualizationError,
    ) as error:
        abort(422, description=str(error))
    return jsonify(package.to_dict())


@core.post("/reports/<dataset_id>/generate")
def generate_report(dataset_id: str):  # type: ignore[no-untyped-def]
    """Generate and retain one evidence-grounded report version."""

    profile = _load_profile(dataset_id)
    try:
        _configuration, _evidence, visualizations, package = (
            _current_report_package(dataset_id, profile)
        )
        draft = generate_narrated_report(
            package,
            model=str(current_app.config["OLLAMA_MODEL"]),
            host=str(current_app.config["OLLAMA_HOST"]),
            timeout_seconds=int(
                current_app.config["OLLAMA_TIMEOUT_SECONDS"]
            ),
        )
        generated, _path = save_generated_report(
            draft,
            generated_report_dir=Path(
                current_app.config["GENERATED_REPORT_DIR"]
            ),
        )
    except (
        BusinessConfigurationError,
        EvidenceError,
        ReportConfigurationError,
        ReportGenerationPackageError,
        ReportNarrationError,
        VisualizationError,
    ) as error:
        current_app.logger.warning(
            "Evidence-grounded report generation unavailable: id=%s "
            "model=%s reason=%s",
            dataset_id,
            current_app.config["OLLAMA_MODEL"],
            error,
        )
        return _redirect_with_state(
            "core.saved_report_configuration",
            {
                "view": "saved_report_configuration",
                "dataset_id": dataset_id,
                "narration_error": str(error),
                "status_code": 503,
            },
            dataset_id=dataset_id,
        )
    return redirect(
        url_for(
            "core.generated_report",
            dataset_id=dataset_id,
            report_id=generated.report_id,
        ),
        code=303,
    )


@core.get("/reports/<dataset_id>/generated")
def latest_report(dataset_id: str):  # type: ignore[no-untyped-def]
    """Open the latest generated report bound to the current package."""

    profile = _load_profile(dataset_id)
    try:
        _configuration, _evidence, _visualizations, package = (
            _current_report_package(dataset_id, profile)
        )
        report = latest_generated_report(
            dataset_id,
            generated_report_dir=Path(
                current_app.config["GENERATED_REPORT_DIR"]
            ),
            expected_package_sha256=artifact_sha256(package.to_dict()),
        )
    except (
        BusinessConfigurationError,
        EvidenceError,
        ReportConfigurationError,
        ReportGenerationPackageError,
        ReportNarrationError,
        VisualizationError,
    ) as error:
        abort(422, description=str(error))
    if report is None:
        abort(404)
    return redirect(
        url_for(
            "core.generated_report",
            dataset_id=dataset_id,
            report_id=report.report_id,
        ),
        code=303,
    )


@core.get("/reports/<dataset_id>/generated/<report_id>")
def generated_report(
    dataset_id: str,
    report_id: str,
):  # type: ignore[no-untyped-def]
    """Render one immutable, source-bound generated report."""

    profile = _load_profile(dataset_id)
    try:
        _configuration, _evidence, _visualizations, package = (
            _current_report_package(dataset_id, profile)
        )
        report = load_generated_report(
            dataset_id,
            report_id,
            generated_report_dir=Path(
                current_app.config["GENERATED_REPORT_DIR"]
            ),
            expected_package_sha256=artifact_sha256(package.to_dict()),
        )
    except (
        BusinessConfigurationError,
        EvidenceError,
        ReportConfigurationError,
        ReportGenerationPackageError,
        ReportNarrationError,
        VisualizationError,
    ) as error:
        abort(422, description=str(error))
    published_stories = included_report_stories(report)
    return render_template(
        "generated_report.html",
        report=report,
        published_stories=published_stories,
        story_sections=_generated_story_sections(published_stories),
        sections=_generated_report_sections(report.items),
        item_by_id={
            item.evidence_id: item for item in report.items
        },
        report_json=json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
    )


@core.post(
    "/reports/<dataset_id>/generated/<report_id>/presentation"
)
def publish_generated_report(
    dataset_id: str,
    report_id: str,
):  # type: ignore[no-untyped-def]
    """Save story inclusion and ordering as a new immutable report version."""

    profile = _load_profile(dataset_id)
    try:
        _configuration, _evidence, _visualizations, package = (
            _current_report_package(dataset_id, profile)
        )
        report = load_generated_report(
            dataset_id,
            report_id,
            generated_report_dir=Path(
                current_app.config["GENERATED_REPORT_DIR"]
            ),
            expected_package_sha256=artifact_sha256(package.to_dict()),
        )
        included_story_ids = tuple(
            request.form.getlist("included_story_ids")
        )
        positions: list[tuple[int, str]] = []
        for story in report.stories:
            raw_position = request.form.get(
                f"story_order_{story.story_id}",
                "",
            )
            try:
                position = int(raw_position)
            except (TypeError, ValueError) as error:
                raise ReportNarrationError(
                    "Every report story requires a numeric display order."
                ) from error
            positions.append((position, story.story_id))
        if (
            any(position < 1 for position, _story_id in positions)
            or len({position for position, _story_id in positions})
            != len(positions)
        ):
            raise ReportNarrationError(
                "Report story display positions must be unique positive "
                "numbers."
            )
        revised = publish_report_presentation(
            report,
            included_story_ids=included_story_ids,
            story_order=tuple(
                story_id
                for _position, story_id in sorted(positions)
            ),
        )
        published, _path = save_generated_report(
            revised,
            generated_report_dir=Path(
                current_app.config["GENERATED_REPORT_DIR"]
            ),
        )
    except (
        BusinessConfigurationError,
        EvidenceError,
        ReportConfigurationError,
        ReportGenerationPackageError,
        ReportNarrationError,
        VisualizationError,
    ) as error:
        abort(400, description=str(error))
    return redirect(
        url_for(
            "core.generated_report",
            dataset_id=dataset_id,
            report_id=published.report_id,
        ),
        code=303,
    )


@core.post(
    "/reports/<dataset_id>/generated/<report_id>/stories/<story_id>/regenerate"
)
def regenerate_report_story(
    dataset_id: str,
    report_id: str,
    story_id: str,
):  # type: ignore[no-untyped-def]
    """Regenerate only one story and append a new immutable version."""

    profile = _load_profile(dataset_id)
    try:
        _configuration, _evidence, _visualizations, package = (
            _current_report_package(dataset_id, profile)
        )
        report = load_generated_report(
            dataset_id,
            report_id,
            generated_report_dir=Path(
                current_app.config["GENERATED_REPORT_DIR"]
            ),
            expected_package_sha256=artifact_sha256(package.to_dict()),
        )
        revised = regenerate_generated_story(
            report,
            package,
            story_id=story_id,
            model=str(current_app.config["OLLAMA_MODEL"]),
            host=str(current_app.config["OLLAMA_HOST"]),
            timeout_seconds=int(
                current_app.config["OLLAMA_TIMEOUT_SECONDS"]
            ),
        )
        regenerated, _path = save_generated_report(
            revised,
            generated_report_dir=Path(
                current_app.config["GENERATED_REPORT_DIR"]
            ),
        )
    except (
        BusinessConfigurationError,
        EvidenceError,
        ReportConfigurationError,
        ReportGenerationPackageError,
        ReportNarrationError,
        VisualizationError,
    ) as error:
        abort(503, description=str(error))
    return redirect(
        url_for(
            "core.generated_report",
            dataset_id=dataset_id,
            report_id=regenerated.report_id,
        ),
        code=303,
    )


@core.get("/reports/<dataset_id>/generated/<report_id>/pdf")
def generated_report_pdf(
    dataset_id: str,
    report_id: str,
):  # type: ignore[no-untyped-def]
    """Download one source-bound report as a print-ready PDF."""

    profile = _load_profile(dataset_id)
    try:
        _configuration, _evidence, visualizations, package = (
            _current_report_package(dataset_id, profile)
        )
        report = load_generated_report(
            dataset_id,
            report_id,
            generated_report_dir=Path(
                current_app.config["GENERATED_REPORT_DIR"]
            ),
            expected_package_sha256=artifact_sha256(package.to_dict()),
        )
        rendered = build_report_pdf(
            report,
            chart_paths=_generated_report_chart_paths(
                report,
                visualizations=visualizations,
            ),
        )
    except (
        BusinessConfigurationError,
        EvidenceError,
        ReportConfigurationError,
        ReportGenerationPackageError,
        ReportNarrationError,
        ReportPdfError,
        VisualizationError,
    ) as error:
        abort(422, description=str(error))
    return send_file(
        io.BytesIO(rendered),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=report_pdf_filename(report),
        max_age=0,
    )


@core.get("/reports/<dataset_id>/generated/<report_id>/json")
def generated_report_json(
    dataset_id: str,
    report_id: str,
):  # type: ignore[no-untyped-def]
    """Return one current generated report artifact as JSON."""

    profile = _load_profile(dataset_id)
    try:
        _configuration, _evidence, _visualizations, package = (
            _current_report_package(dataset_id, profile)
        )
        report = load_generated_report(
            dataset_id,
            report_id,
            generated_report_dir=Path(
                current_app.config["GENERATED_REPORT_DIR"]
            ),
            expected_package_sha256=artifact_sha256(package.to_dict()),
        )
    except (
        BusinessConfigurationError,
        EvidenceError,
        ReportConfigurationError,
        ReportGenerationPackageError,
        ReportNarrationError,
        VisualizationError,
    ) as error:
        abort(422, description=str(error))
    return jsonify(report.to_dict())


@core.get("/visualizations/<dataset_id>")
def saved_visualizations(dataset_id: str):  # type: ignore[no-untyped-def]
    """List user-created KPI and supplementary visualizations."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
        visualizations = list_visualizations(
            dataset_id=dataset_id,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
            profile=profile,
            configuration=configuration,
        )
    except (BusinessConfigurationError, VisualizationError) as error:
        abort(422, description=str(error))
    return render_template(
        "visualizations.html",
        dataset_id=dataset_id,
        visualizations=visualizations,
    )


@core.get("/visualizations/<dataset_id>/new")
def visualization_builder(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display the stable manual visualization builder."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
    except BusinessConfigurationError as error:
        abort(422, description=str(error))
    state = _load_view_state("visualization_builder", dataset_id=dataset_id)
    state_form = _form_from_state(state.get("form_data"))
    form_data = state_form or _default_visualization_form(profile)
    edit_id = request.args.get("edit", "")
    if state_form is None and edit_id:
        try:
            artifact = load_visualization(
                edit_id,
                dataset_id=dataset_id,
                visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
                profile=profile,
                configuration=configuration,
            )
            form_data = _visualization_form_from_artifact(artifact)
        except VisualizationError as error:
            abort(404, description=str(error))
    return (
        render_template(
            "visualization_builder.html",
            dataset_id=dataset_id,
            profile=profile,
            configuration=configuration,
            form_data=form_data,
            error=_state_text(state, "error"),
            chart_types=CHART_TYPES,
            aggregations=AGGREGATIONS,
            date_granularities=DATE_GRANULARITIES,
            filter_modes=FILTER_MODES,
            scales=SCALES,
            numeric_columns=tuple(
                column.name
                for column in profile.columns
                if column.inferred_type.value == "numeric"
                and not column.is_constant
                and not column.is_empty
            ),
        ),
        _state_status(state),
    )


@core.post("/visualizations/<dataset_id>/preview")
def preview_visualization(dataset_id: str):  # type: ignore[no-untyped-def]
    """Validate and generate a short-lived visualization preview."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
        values = _visualization_request_values(request.form)
        spec = parse_visualization_spec(values)
        if spec.replaces_visualization_id is not None:
            load_visualization(
                spec.replaces_visualization_id,
                dataset_id=dataset_id,
                visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
                profile=profile,
                configuration=configuration,
            )
        artifact = build_visualization(
            _load_dataset_view_for_id(dataset_id),
            profile=profile,
            configuration=configuration,
            spec=spec,
            chart_dir=Path(current_app.config["CHART_DIR"]),
        )
        token = save_preview(
            artifact,
            preview_dir=Path(current_app.config["VISUALIZATION_PREVIEW_DIR"]),
            chart_dir=Path(current_app.config["CHART_DIR"]),
        )
    except (BusinessConfigurationError, VisualizationError) as error:
        return _redirect_with_state(
            "core.visualization_builder",
            {
                "view": "visualization_builder",
                "dataset_id": dataset_id,
                "form_data": _form_to_state(request.form),
                "error": str(error),
                "status_code": 400,
            },
            dataset_id=dataset_id,
        )
    return redirect(
        url_for(
            "core.visualization_preview",
            dataset_id=dataset_id,
            token=token,
        ),
        code=303,
    )


@core.get("/visualizations/<dataset_id>/preview/<token>")
def visualization_preview(dataset_id: str, token: str):  # type: ignore[no-untyped-def]
    """Display one validated draft from a reloadable GET URL."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
        artifact = load_preview(
            token,
            dataset_id=dataset_id,
            preview_dir=Path(current_app.config["VISUALIZATION_PREVIEW_DIR"]),
            profile=profile,
            configuration=configuration,
        )
    except (BusinessConfigurationError, VisualizationError) as error:
        abort(404, description=str(error))
    return _render_visualization(
        artifact,
        preview_token=token,
        artifact_json=json.dumps(artifact.to_dict(), indent=2, sort_keys=True),
    )


@core.get("/visualizations/<dataset_id>/preview/<token>/chart")
def visualization_preview_chart(
    dataset_id: str, token: str
):  # type: ignore[no-untyped-def]
    """Serve a draft chart only through its validated preview token."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
        artifact = load_preview(
            token,
            dataset_id=dataset_id,
            preview_dir=Path(current_app.config["VISUALIZATION_PREVIEW_DIR"]),
            profile=profile,
            configuration=configuration,
        )
        filename = artifact_chart_filename(artifact)
    except (BusinessConfigurationError, VisualizationError) as error:
        abort(404, description=str(error))
    return _send_visualization_chart(filename)


@core.post("/visualizations/<dataset_id>/preview/<token>/save")
def confirm_visualization(dataset_id: str, token: str):  # type: ignore[no-untyped-def]
    """Save a validated draft as a stable report-selectable visualization."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
        artifact = load_preview(
            token,
            dataset_id=dataset_id,
            preview_dir=Path(current_app.config["VISUALIZATION_PREVIEW_DIR"]),
            profile=profile,
            configuration=configuration,
        )
        previous = None
        if artifact.spec.replaces_visualization_id is not None:
            previous = load_visualization(
                artifact.spec.replaces_visualization_id,
                dataset_id=dataset_id,
                visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
                profile=profile,
                configuration=configuration,
            )
        saved, _path = save_visualization(
            artifact,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
            previous=previous,
        )
        delete_preview(
            token,
            preview_dir=Path(current_app.config["VISUALIZATION_PREVIEW_DIR"]),
        )
        if previous is not None:
            delete_chart(
                previous.chart.filename,
                chart_dir=Path(current_app.config["CHART_DIR"]),
            )
    except (BusinessConfigurationError, VisualizationError) as error:
        abort(422, description=str(error))
    return redirect(
        url_for(
            "core.saved_visualization",
            dataset_id=dataset_id,
            visualization_id=saved.visualization_id,
        ),
        code=303,
    )


@core.get("/visualizations/<dataset_id>/<visualization_id>")
def saved_visualization(
    dataset_id: str, visualization_id: str
):  # type: ignore[no-untyped-def]
    """Display a saved visualization and its reproducible supporting data."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
        artifact = load_visualization(
            visualization_id,
            dataset_id=dataset_id,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
            profile=profile,
            configuration=configuration,
        )
    except (BusinessConfigurationError, VisualizationError) as error:
        abort(404, description=str(error))
    return _render_visualization(
        artifact,
        artifact_json=json.dumps(artifact.to_dict(), indent=2, sort_keys=True),
    )


@core.get("/visualizations/<dataset_id>/<visualization_id>/chart")
def saved_visualization_chart(
    dataset_id: str, visualization_id: str
):  # type: ignore[no-untyped-def]
    """Serve a saved chart only through its validated visualization record."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
        artifact = load_visualization(
            visualization_id,
            dataset_id=dataset_id,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
            profile=profile,
            configuration=configuration,
        )
        filename = artifact_chart_filename(artifact)
    except (BusinessConfigurationError, VisualizationError) as error:
        abort(404, description=str(error))
    return _send_visualization_chart(filename)


@core.post("/visualizations/<dataset_id>/<visualization_id>/regenerate")
def regenerate_visualization(
    dataset_id: str, visualization_id: str
):  # type: ignore[no-untyped-def]
    """Recalculate a saved visualization from its retained source and specification."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
        previous = load_visualization(
            visualization_id,
            dataset_id=dataset_id,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
            profile=profile,
            configuration=configuration,
        )
        spec = replace(
            previous.spec,
            replaces_visualization_id=visualization_id,
        )
        regenerated = build_visualization(
            _load_dataset_view_for_id(dataset_id),
            profile=profile,
            configuration=configuration,
            spec=spec,
            chart_dir=Path(current_app.config["CHART_DIR"]),
            assistant_metadata=previous.assistant,
        )
        try:
            saved, _path = save_visualization(
                regenerated,
                visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
                previous=previous,
            )
        except VisualizationError:
            delete_chart(
                regenerated.chart.filename,
                chart_dir=Path(current_app.config["CHART_DIR"]),
            )
            raise
        delete_chart(
            previous.chart.filename,
            chart_dir=Path(current_app.config["CHART_DIR"]),
        )
    except (BusinessConfigurationError, VisualizationError) as error:
        abort(422, description=str(error))
    return redirect(
        url_for(
            "core.saved_visualization",
            dataset_id=dataset_id,
            visualization_id=saved.visualization_id,
        ),
        code=303,
    )


@core.post("/insights/<dataset_id>")
def deterministic_insights(dataset_id: str):  # type: ignore[no-untyped-def]
    """Generate factual observations, evidence records, and charts using Python."""

    profile = _load_profile(dataset_id)
    configuration_path = Path(current_app.config["CONFIGURATION_DIR"]) / (
        f"{dataset_id}.json"
    )
    if not configuration_path.is_file():
        abort(404)

    try:
        configuration = load_business_configuration(configuration_path, profile=profile)
        view = _load_dataset_view_for_id(dataset_id)
        report = generate_insights(
            view,
            profile=profile,
            configuration=configuration,
        )
        evidence = generate_evidence(
            view,
            profile=profile,
            configuration=configuration,
            insight_report=report,
            chart_dir=Path(current_app.config["CHART_DIR"]),
        )
        evidence_path = Path(current_app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json"
        previous_payload = None
        if evidence_path.is_file():
            try:
                previous_payload = load_evidence_payload(
                    evidence_path,
                    dataset_id=dataset_id,
                )
            except EvidenceError:
                current_app.logger.warning(
                    "Previous evidence report is unreadable: id=%s", dataset_id
                )
        new_chart_filenames = referenced_chart_filenames(evidence.to_dict())
        try:
            report_path = save_insight_report(
                report,
                insight_dir=Path(current_app.config["INSIGHT_DIR"]),
            )
            saved_evidence_path = save_evidence_report(
                evidence,
                evidence_dir=Path(current_app.config["EVIDENCE_DIR"]),
            )
        except (EvidenceError, OSError) as error:
            delete_chart_files(
                Path(current_app.config["CHART_DIR"]),
                new_chart_filenames,
            )
            if isinstance(error, EvidenceError):
                raise
            raise EvidenceError("Evidence artifacts could not be saved.") from error
        delete_chart_files(
            Path(current_app.config["CHART_DIR"]),
            referenced_chart_filenames(previous_payload),
        )
    except (BusinessConfigurationError, InsightEngineError, EvidenceError) as error:
        current_app.logger.warning(
            "Deterministic evidence rejected: id=%s reason=%s", dataset_id, error
        )
        abort(422, description=str(error))

    current_app.logger.info(
        "Deterministic evidence generated: id=%s insights=%d records=%d paths=%s,%s",
        dataset_id,
        len(report.insights),
        len(evidence.records),
        report_path.name,
        saved_evidence_path.name,
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
    evidence_path = Path(current_app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json"
    evidence = None
    if evidence_path.is_file():
        try:
            evidence = load_evidence_payload(evidence_path, dataset_id=dataset_id)
        except EvidenceError as error:
            abort(422, description=str(error))
    return render_template(
        "insights.html",
        report=report,
        report_json=json.dumps(report, indent=2, sort_keys=True),
        evidence=evidence,
        evidence_json=(
            json.dumps(evidence, indent=2, sort_keys=True)
            if evidence is not None
            else None
        ),
    )


@core.get("/evidence/<dataset_id>/<evidence_id>/chart")
def evidence_chart(dataset_id: str, evidence_id: str):  # type: ignore[no-untyped-def]
    """Serve one chart only when referenced by the dataset's evidence record."""

    _dataset_path(dataset_id)
    evidence_path = Path(current_app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json"
    if not evidence_path.is_file():
        abort(404)
    try:
        payload = load_evidence_payload(evidence_path, dataset_id=dataset_id)
    except EvidenceError as error:
        abort(422, description=str(error))
    filename = chart_filename_for(payload, evidence_id=evidence_id)
    if filename is None:
        abort(404)
    chart_path = Path(current_app.config["CHART_DIR"]) / filename
    if not chart_path.is_file():
        abort(404)
    return send_from_directory(
        Path(current_app.config["CHART_DIR"]),
        filename,
        mimetype="image/png",
        conditional=True,
        max_age=0,
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
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
    except BusinessConfigurationError:
        configuration = None
    dataset_context = _build_dataset_context(
        dataset_id,
        profile,
        configuration=configuration,
    )
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
            dataset_context=dataset_context,
            configuration=configuration,
            unconfigured_source_columns=(
                tuple(
                    candidate
                    for candidate in profile.kpi_candidates
                    if candidate.casefold()
                    not in {
                        metric.name.casefold()
                        for metric in configuration.metrics
                    }
                )
                if configuration is not None
                else profile.kpi_candidates
            ),
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
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
    except BusinessConfigurationError:
        configuration = None
    dataset_context = _build_dataset_context(
        dataset_id,
        profile,
        configuration=configuration,
    )
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
            dataset_context=dataset_context,
            configuration=configuration,
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


def _visualization_request_values(
    values: MultiDict[str, str],
) -> dict[str, object]:
    return {
        "title": values.get("title", ""),
        "purpose": values.get("purpose", ""),
        "chart_type": values.get("chart_type", ""),
        "measure_selectors": values.getlist("measure_selectors"),
        "x_column": values.get("x_column", ""),
        "series_column": values.get("series_column", ""),
        "aggregation": values.get("aggregation", ""),
        "date_granularity": values.get("date_granularity", ""),
        "filter_column": values.get("filter_column", ""),
        "filter_mode": values.get("filter_mode", ""),
        "filter_values": values.get("filter_values", ""),
        "date_start": values.get("date_start", ""),
        "date_end": values.get("date_end", ""),
        "sort_by": values.get("sort_by", ""),
        "sort_direction": values.get("sort_direction", ""),
        "top_n": values.get("top_n", ""),
        "scale": values.get("scale", ""),
        "bin_count": values.get("bin_count", ""),
        "include_in_report": values.get("include_in_report", ""),
        "replaces_visualization_id": values.get(
            "replaces_visualization_id", ""
        ),
    }


def _default_visualization_form(profile: DatasetProfile) -> MultiDict[str, str]:
    values = MultiDict[str, str]()
    values["title"] = "Manual dataset visualization"
    values["purpose"] = ""
    values["chart_type"] = "category_bar"
    values["aggregation"] = "configured"
    values["date_granularity"] = "month"
    values["filter_mode"] = "include"
    values["sort_by"] = "value"
    values["sort_direction"] = "descending"
    values["top_n"] = "10"
    values["scale"] = "linear"
    values["bin_count"] = "10"
    values["include_in_report"] = "yes"
    if profile.category_candidates:
        values["x_column"] = profile.category_candidates[0]
    return values


def _visualization_form_from_artifact(
    artifact: VisualizationArtifact,
) -> MultiDict[str, str]:
    values = spec_to_form(artifact)
    form = MultiDict[str, str]()
    for key, value in values.items():
        if key == "measure_selectors" and isinstance(value, list):
            form.setlist(key, [str(item) for item in value])
        else:
            form[key] = str(value) if value is not None else ""
    return form


def _build_dataset_context(
    dataset_id: str,
    profile: DatasetProfile,
    *,
    configuration: BusinessConfiguration | None,
) -> DatasetContext:
    view = _load_dataset_view_for_id(dataset_id)
    visualizations: tuple[VisualizationArtifact, ...] = ()
    evidence = None
    if configuration is not None:
        try:
            visualizations = list_visualizations(
                dataset_id=dataset_id,
                visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
                profile=profile,
                configuration=configuration,
            )
        except VisualizationError as error:
            current_app.logger.info(
                "Saved visualizations excluded from dataset context: id=%s reason=%s",
                dataset_id,
                error,
            )
        evidence_path = (
            Path(current_app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json"
        )
        if evidence_path.is_file():
            try:
                evidence = load_evidence_payload(
                    evidence_path,
                    dataset_id=dataset_id,
                )
            except EvidenceError as error:
                current_app.logger.info(
                    "Evidence excluded from dataset context: id=%s reason=%s",
                    dataset_id,
                    error,
                )
    return build_dataset_context(
        view,
        profile=profile,
        configuration=configuration,
        saved_visualizations=visualizations,
        evidence_payload=evidence,
    )


def _report_assets(
    dataset_id: str,
    profile: DatasetProfile,
) -> tuple[
    BusinessConfiguration,
    dict[str, object] | None,
    tuple[VisualizationArtifact, ...],
]:
    configuration = _require_configuration(dataset_id, profile)
    evidence_path = (
        Path(current_app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json"
    )
    evidence = (
        load_evidence_payload(evidence_path, dataset_id=dataset_id)
        if evidence_path.is_file()
        else None
    )
    visualizations = list_visualizations(
        dataset_id=dataset_id,
        visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
        profile=profile,
        configuration=configuration,
    )
    return configuration, evidence, visualizations


def _current_report_package(
    dataset_id: str,
    profile: DatasetProfile,
) -> tuple[
    BusinessConfiguration,
    dict[str, object] | None,
    tuple[VisualizationArtifact, ...],
    ReportGenerationPackage,
]:
    configuration, evidence, visualizations = _report_assets(
        dataset_id,
        profile,
    )
    report_path = _report_configuration_path(dataset_id)
    if not report_path.is_file():
        raise ReportConfigurationError(
            "Configure and save report content before generating narration."
        )
    report = load_report_configuration(
        report_path,
        configuration=configuration,
        evidence_payload=evidence,
        visualizations=visualizations,
    )
    package = build_report_generation_package(
        report,
        configuration=configuration,
        evidence_payload=evidence,
        visualizations=visualizations,
    )
    return configuration, evidence, visualizations, package


def _generated_report_chart_paths(
    report: GeneratedReport,
    *,
    visualizations: tuple[VisualizationArtifact, ...],
) -> dict[str, Path]:
    chart_dir = Path(current_app.config["CHART_DIR"])
    visualization_by_id = {
        artifact.visualization_id: artifact
        for artifact in visualizations
        if artifact.visualization_id is not None
    }
    chart_paths: dict[str, Path] = {}
    for item in report.items:
        filename = item.chart_filename
        if filename is None and item.visualization_id is not None:
            artifact = visualization_by_id.get(item.visualization_id)
            if artifact is not None:
                filename = artifact_chart_filename(artifact)
        if filename is None:
            continue
        chart_path = chart_dir / filename
        if chart_path.is_file():
            chart_paths[item.evidence_id] = chart_path
    return chart_paths


def _generated_report_sections(
    items: tuple[NarratedEvidence, ...],
) -> tuple[tuple[str, tuple[NarratedEvidence, ...]], ...]:
    labels = (
        ("key_findings", "Key findings"),
        ("trends_and_changes", "Trends and changes"),
        ("segment_analysis", "Segment analysis"),
        ("anomalies", "Anomalies"),
        ("associations", "Associations"),
        ("benchmarks", "Benchmarks and thresholds"),
        ("manual_visualizations", "Manual visualizations"),
        (
            "data_quality_and_limitations",
            "Data quality and analysis limitations",
        ),
    )
    return tuple(
        (
            label,
            tuple(item for item in items if item.section == section),
        )
        for section, label in labels
        if any(item.section == section for item in items)
    )


def _generated_story_sections(
    stories: tuple[NarrativeStory, ...],
) -> tuple[tuple[str, tuple[NarrativeStory, ...]], ...]:
    labels = (
        ("key_findings", "Key findings"),
        ("trends_and_changes", "Trends and changes"),
        ("segment_analysis", "Segment analysis"),
        ("anomalies", "Anomalies"),
        ("associations", "Associations"),
        ("benchmarks", "Benchmarks and thresholds"),
        ("manual_visualizations", "Manual visualizations"),
        (
            "data_quality_and_limitations",
            "Data quality and analysis limitations",
        ),
    )
    return tuple(
        (
            label,
            tuple(
                story for story in stories if story.section == section
            ),
        )
        for section, label in labels
        if any(story.section == section for story in stories)
    )


def _sorted_evidence_records(
    evidence_payload: dict[str, object] | None,
) -> tuple[dict[str, object], ...]:
    if evidence_payload is None:
        return ()
    records = evidence_payload.get("records")
    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        raise EvidenceError("Saved evidence records are invalid.")

    def sort_key(record: dict[str, object]) -> tuple[int, str]:
        ranking = record.get("ranking")
        rank = ranking.get("rank") if isinstance(ranking, dict) else None
        return (
            rank if isinstance(rank, int) and rank > 0 else 1_000_000,
            str(record.get("id", "")),
        )

    return tuple(sorted(records, key=sort_key))


def _default_report_form(
    configuration: BusinessConfiguration,
    *,
    evidence_payload: dict[str, object] | None,
    visualizations: tuple[VisualizationArtifact, ...],
) -> MultiDict[str, str]:
    values = MultiDict[str, str]()
    values["title"] = f"{configuration.primary_metric.name} insight report"
    values["company_name"] = ""
    values["report_author"] = ""
    values["business_objective"] = configuration.business_objective
    values["audience"] = "management"
    values["tone"] = "professional"
    values["detail_level"] = "standard"
    values["include_evidence_appendix"] = "yes"
    values.setlist(
        "selected_metric_ids",
        [metric.metric_id for metric in configuration.metrics],
    )
    values.setlist(
        "selected_evidence_ids",
        [
            str(record["id"])
            for record in _sorted_evidence_records(evidence_payload)
            if isinstance(record.get("id"), str)
        ],
    )
    values.setlist(
        "selected_visualization_ids",
        [
            artifact.visualization_id
            for artifact in visualizations
            if artifact.visualization_id is not None
            and artifact.spec.include_in_report
        ],
    )
    return values


def _report_form_from_configuration(
    report: ReportConfiguration,
) -> MultiDict[str, str]:
    values = MultiDict[str, str]()
    values["title"] = report.title
    values["company_name"] = report.company_name
    values["report_author"] = report.report_author
    values["business_objective"] = report.business_objective
    values["audience"] = report.audience
    values["tone"] = report.tone
    values["detail_level"] = report.detail_level
    values["user_notes"] = report.user_notes
    values["include_evidence_appendix"] = (
        "yes" if report.include_evidence_appendix else ""
    )
    values.setlist(
        "selected_metric_ids",
        list(report.selected_metric_ids),
    )
    values.setlist(
        "selected_evidence_ids",
        list(report.selected_evidence_ids),
    )
    values.setlist(
        "selected_visualization_ids",
        list(report.selected_visualization_ids),
    )
    return values


def _report_configuration_path(dataset_id: str) -> Path:
    return (
        Path(current_app.config["REPORT_CONFIGURATION_DIR"])
        / f"{dataset_id}.json"
    )


def _render_visualization(
    artifact: VisualizationArtifact,
    *,
    artifact_json: str,
    preview_token: str | None = None,
):  # type: ignore[no-untyped-def]
    return render_template(
        "visualization.html",
        artifact=artifact,
        artifact_json=artifact_json,
        preview_token=preview_token,
    )


def _send_visualization_chart(filename: str):  # type: ignore[no-untyped-def]
    chart_path = Path(current_app.config["CHART_DIR"]) / filename
    if not chart_path.is_file():
        abort(404)
    return send_from_directory(
        Path(current_app.config["CHART_DIR"]),
        filename,
        mimetype="image/png",
        conditional=True,
        max_age=0,
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
