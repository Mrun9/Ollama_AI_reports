"""HTTP orchestration for the local reporting workflow.

Routes translate requests into calls to the domain modules. Calculations,
validation, persistence, charting, and model interaction belong in those
modules rather than here.
"""

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
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from insight_reporter.business_config import (
    DISPLAY_FORMATS,
    SOURCE_AGGREGATIONS,
    TARGET_SCOPES,
    BusinessConfiguration,
    BusinessConfigurationError,
    add_source_metrics,
    load_business_configuration,
    remove_metric,
    save_business_configuration,
    set_primary_metric,
    update_metric_settings,
    validate_business_configuration,
    validate_conditional_business_configuration,
    validate_derived_business_configuration,
)
from insight_reporter.conditional_metrics import (
    ConditionalMetricError,
    condition_value_options,
    validate_conditional_metric,
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
    DatasetUploadResult,
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
from insight_reporter.manual_visualization_preview import (
    ManualVisualizationPreviewError,
    build_manual_visualization_preview,
)
from insight_reporter.manual_visualization_store import (
    ManualVisualizationArtifact,
    ManualVisualizationStoreError,
    list_manual_visualizations,
    load_manual_visualization,
    manual_visualization_png_path,
    manual_visualization_svg_path,
    save_manual_visualization,
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
    generated_report_chart_snapshots,
    included_executive_summary_points,
    included_report_stories,
    latest_generated_report,
    list_generated_report_versions,
    load_generated_report,
    load_generated_report_version,
    publish_report_presentation,
    regenerate_generated_story,
    save_generated_report,
    snapshot_generated_report_charts,
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
from insight_reporter.visualization_insights import (
    VisualizationInsightArtifact,
    VisualizationInsightError,
    generate_visualization_insight,
    load_visualization_insight,
    save_visualization_insight,
    set_visualization_insight_report_inclusion,
)
from insight_reporter.visualization_suggestions import (
    VisualizationSuggestionError,
    generate_visualization_suggestion,
)
from insight_reporter.workspace_history import (
    WorkspaceDirectories,
    WorkspaceHistoryError,
    WorkspaceSummary,
    archive_workspace,
    archive_workspace_report,
    archive_workspace_source,
    attach_workspace_source,
    create_empty_workspace,
    create_workspace_record,
    get_workspace_summary,
    list_workspace_summaries,
    load_workspace_record,
    rename_workspace_report,
    rename_workspace_source,
    restore_workspace,
    restore_workspace_report,
    restore_workspace_source,
    update_workspace_details,
)

core = Blueprint("core", __name__)

_DIAGNOSTIC_EVIDENCE_TYPES = frozenset(
    {
        "missing_data_warning",
        "insufficient_data_warning",
        "analysis_skipped",
    }
)
_ASSOCIATION_EVIDENCE_TYPES = frozenset({"numeric_correlation"})
_MAX_DEFAULT_EVIDENCE = 10
_MAX_DEFAULT_ASSOCIATIONS = 2


# Persistent workspaces and history


@core.get("/workspaces")
def workspace_history():  # type: ignore[no-untyped-def]
    """List active and recoverably archived local workspaces."""

    try:
        all_workspaces = list_workspace_summaries(
            directories=_workspace_directories(),
            include_archived=True,
        )
    except WorkspaceHistoryError as error:
        abort(422, description=str(error))
    return render_template(
        "workspaces.html",
        workspaces=tuple(item for item in all_workspaces if not item.record.is_archived),
        archived_workspaces=tuple(item for item in all_workspaces if item.record.is_archived),
    )


@core.post("/workspaces")
def create_workspace():  # type: ignore[no-untyped-def]
    """Create an empty workspace before any source is selected."""

    try:
        workspace = create_empty_workspace(
            request.form.get("name"),
            description=request.form.get("description", ""),
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
        )
    except WorkspaceHistoryError as error:
        abort(400, description=str(error))
    return redirect(
        url_for(
            "core.workspace_detail",
            dataset_id=workspace.dataset_id,
        ),
        code=303,
    )


@core.get("/workspaces/<dataset_id>")
def workspace_detail(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display one persistent workspace and its available artifacts."""

    try:
        workspace = get_workspace_summary(
            dataset_id,
            directories=_workspace_directories(),
        )
    except WorkspaceHistoryError as error:
        abort(422, description=str(error))
    if workspace is None:
        abort(404)
    try:
        report_versions = list_generated_report_versions(
            dataset_id,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
        )
    except ReportNarrationError as error:
        abort(422, description=str(error))
    latest_by_report_id: dict[str, GeneratedReport] = {}
    for report in report_versions:
        latest_by_report_id.setdefault(report.report_id, report)
    active_reports = tuple(
        report
        for report_id, report in latest_by_report_id.items()
        if report_id not in workspace.record.archived_report_ids
    )
    archived_reports = tuple(
        report
        for report_id, report in latest_by_report_id.items()
        if report_id in workspace.record.archived_report_ids
    )
    configuration_ready = _configuration_path(dataset_id).is_file()
    evidence_ready = (Path(current_app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json").is_file()
    visualization_count = 0
    configuration = None
    if workspace.record.has_source:
        try:
            profile = _load_profile(dataset_id)
            configuration = _load_existing_configuration(
                dataset_id,
                profile,
            )
            automated_count = len(
                list_visualizations(
                    dataset_id=dataset_id,
                    visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
                    profile=profile,
                    configuration=configuration,
                )
            )
            manual_count = len(
                list_manual_visualizations(
                    dataset_id=dataset_id,
                    source_sha256=profile.source_sha256,
                    visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
                )
            )
            visualization_count = automated_count + manual_count
        except (
            BusinessConfigurationError,
            ManualVisualizationStoreError,
            VisualizationError,
        ):
            visualization_count = 0
    return render_template(
        "workspace.html",
        workspace=workspace,
        resume_url=_workspace_resume_url(workspace),
        create_report_url=_workspace_create_report_url(workspace),
        reports=active_reports,
        archived_reports=archived_reports,
        configuration_ready=configuration_ready,
        configuration=configuration,
        evidence_ready=evidence_ready,
        visualization_count=visualization_count,
        report_configuration_ready=_report_configuration_path(dataset_id).is_file(),
    )


@core.post("/workspaces/<dataset_id>/name")
def update_workspace_name(dataset_id: str):  # type: ignore[no-untyped-def]
    """Persist editable workspace metadata without changing identity."""

    try:
        workspace = get_workspace_summary(
            dataset_id,
            directories=_workspace_directories(),
        )
        if workspace is None:
            abort(404)
        update_workspace_details(
            dataset_id,
            name=request.form.get("name"),
            description=request.form.get("description", ""),
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
            fallback_record=workspace.record,
        )
    except WorkspaceHistoryError as error:
        abort(400, description=str(error))
    return redirect(
        url_for("core.workspace_detail", dataset_id=dataset_id),
        code=303,
    )


@core.post("/workspaces/<dataset_id>/archive")
def delete_workspace(dataset_id: str):  # type: ignore[no-untyped-def]
    """Soft-delete a workspace without deleting its artifacts."""

    try:
        _materialize_workspace_metadata(dataset_id)
        archive_workspace(
            dataset_id,
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
        )
    except WorkspaceHistoryError as error:
        abort(400, description=str(error))
    return redirect(url_for("core.workspace_history"), code=303)


@core.post("/workspaces/<dataset_id>/restore")
def undelete_workspace(dataset_id: str):  # type: ignore[no-untyped-def]
    """Restore a soft-deleted workspace."""

    try:
        restore_workspace(
            dataset_id,
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
        )
    except WorkspaceHistoryError as error:
        abort(400, description=str(error))
    return redirect(
        url_for("core.workspace_detail", dataset_id=dataset_id),
        code=303,
    )


@core.get("/workspaces/<dataset_id>/source")
def workspace_source_form(dataset_id: str):  # type: ignore[no-untyped-def]
    """Show the source-selection form for an empty workspace."""

    workspace = _required_workspace_summary(dataset_id)
    if (
        workspace.record.has_source
        or workspace.record.source_archived_at is not None
        or workspace.record.is_archived
    ):
        return redirect(
            url_for("core.workspace_detail", dataset_id=dataset_id),
            code=303,
        )
    return render_template(
        "workspace_source.html",
        workspace=workspace,
        **_upload_limits(),
    )


@core.post("/workspaces/<dataset_id>/source")
def add_workspace_source(dataset_id: str):  # type: ignore[no-untyped-def]
    """Validate and attach one source to an existing empty workspace."""

    workspace = _required_workspace_summary(dataset_id)
    if workspace.record.has_source:
        abort(409, description="This workspace already has a data source.")
    uploaded_files = [
        item for field_name in request.files for item in request.files.getlist(field_name)
    ]
    if len(uploaded_files) != 1 or "file" not in request.files:
        abort(
            400,
            description="Select exactly one CSV, JSON, or XLSX file.",
        )
    uploaded_file = request.files["file"]
    result: DatasetUploadResult | None = None
    try:
        result = ingest_dataset(
            uploaded_file,
            upload_dir=Path(current_app.config["UPLOAD_DIR"]),
            max_bytes=int(current_app.config["MAX_UPLOAD_BYTES"]),
            max_rows=int(current_app.config["MAX_CSV_ROWS"]),
            max_columns=int(current_app.config["MAX_CSV_COLUMNS"]),
            dataset_id=dataset_id,
        )
        attach_workspace_source(
            dataset_id,
            result,
            original_filename=uploaded_file.filename,
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
        )
    except (DatasetValidationError, WorkspaceHistoryError) as error:
        if result is not None:
            (Path(current_app.config["UPLOAD_DIR"]) / result.internal_filename).unlink(
                missing_ok=True
            )
        abort(
            error.status_code if isinstance(error, DatasetValidationError) else 400,
            description=str(error),
        )
    assert result is not None
    if result.source_format == "xlsx":
        if result.requires_table_selection:
            return redirect(
                url_for(
                    "core.excel_sheet_selection",
                    dataset_id=dataset_id,
                ),
                code=303,
            )
        save_xlsx_selection(
            Path(current_app.config["UPLOAD_DIR"]),
            dataset_id,
            result.table_names[0],
        )
    return redirect(
        url_for("core.dataset_profile", dataset_id=dataset_id),
        code=303,
    )


@core.post("/workspaces/<dataset_id>/source/name")
def update_workspace_source_name(
    dataset_id: str,
):  # type: ignore[no-untyped-def]
    """Edit a data source's display label."""

    try:
        _materialize_workspace_metadata(dataset_id)
        rename_workspace_source(
            dataset_id,
            request.form.get("name"),
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
        )
    except WorkspaceHistoryError as error:
        abort(400, description=str(error))
    return redirect(
        url_for("core.workspace_detail", dataset_id=dataset_id),
        code=303,
    )


@core.post("/workspaces/<dataset_id>/source/archive")
def delete_workspace_source(
    dataset_id: str,
):  # type: ignore[no-untyped-def]
    """Move a source to recoverable trash while keeping report history."""

    try:
        _materialize_workspace_metadata(dataset_id)
        archive_workspace_source(
            dataset_id,
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
            upload_dir=Path(current_app.config["UPLOAD_DIR"]),
            trash_dir=Path(current_app.config["TRASH_DIR"]),
        )
    except WorkspaceHistoryError as error:
        abort(400, description=str(error))
    return redirect(
        url_for("core.workspace_detail", dataset_id=dataset_id),
        code=303,
    )


@core.post("/workspaces/<dataset_id>/source/restore")
def undelete_workspace_source(
    dataset_id: str,
):  # type: ignore[no-untyped-def]
    """Restore a data source from recoverable trash."""

    try:
        restore_workspace_source(
            dataset_id,
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
            upload_dir=Path(current_app.config["UPLOAD_DIR"]),
            trash_dir=Path(current_app.config["TRASH_DIR"]),
        )
    except WorkspaceHistoryError as error:
        abort(400, description=str(error))
    return redirect(
        url_for("core.workspace_detail", dataset_id=dataset_id),
        code=303,
    )


@core.post("/workspaces/<dataset_id>/reports/<report_id>/name")
def update_workspace_report_name(
    dataset_id: str,
    report_id: str,
):  # type: ignore[no-untyped-def]
    """Rename a report through mutable workspace metadata."""

    _require_report_run(dataset_id, report_id)
    try:
        _materialize_workspace_metadata(dataset_id)
        rename_workspace_report(
            dataset_id,
            report_id,
            request.form.get("name"),
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
        )
    except WorkspaceHistoryError as error:
        abort(400, description=str(error))
    return redirect(
        url_for("core.workspace_detail", dataset_id=dataset_id),
        code=303,
    )


@core.post("/workspaces/<dataset_id>/reports/<report_id>/archive")
def delete_workspace_report(
    dataset_id: str,
    report_id: str,
):  # type: ignore[no-untyped-def]
    """Soft-delete one immutable report run."""

    _require_report_run(dataset_id, report_id)
    try:
        _materialize_workspace_metadata(dataset_id)
        archive_workspace_report(
            dataset_id,
            report_id,
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
        )
    except WorkspaceHistoryError as error:
        abort(400, description=str(error))
    return redirect(
        url_for("core.workspace_detail", dataset_id=dataset_id),
        code=303,
    )


@core.post("/workspaces/<dataset_id>/reports/<report_id>/restore")
def undelete_workspace_report(
    dataset_id: str,
    report_id: str,
):  # type: ignore[no-untyped-def]
    """Restore one soft-deleted report run."""

    _require_report_run(dataset_id, report_id)
    try:
        restore_workspace_report(
            dataset_id,
            report_id,
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
        )
    except WorkspaceHistoryError as error:
        abort(400, description=str(error))
    return redirect(
        url_for("core.workspace_detail", dataset_id=dataset_id),
        code=303,
    )


# Dataset ingestion, profiling, and KPI configuration


def _upload_limits() -> dict[str, int]:
    return {
        "max_bytes": int(current_app.config["MAX_UPLOAD_BYTES"]),
        "max_rows": int(current_app.config["MAX_CSV_ROWS"]),
        "max_columns": int(current_app.config["MAX_CSV_COLUMNS"]),
    }


@core.get("/")
def home():  # type: ignore[no-untyped-def]
    """Start every session from persistent workspace history."""

    return redirect(url_for("core.workspace_history"), code=302)


@core.get("/upload")
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
        item for field_name in request.files for item in request.files.getlist(field_name)
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

    uploaded_file = request.files["file"]
    try:
        result = ingest_dataset(
            uploaded_file,
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

    try:
        create_workspace_record(
            result,
            original_filename=uploaded_file.filename,
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
        )
    except WorkspaceHistoryError as error:
        (Path(current_app.config["UPLOAD_DIR"]) / result.internal_filename).unlink(missing_ok=True)
        current_app.logger.error(
            "Workspace metadata could not be created: id=%s",
            result.internal_filename,
        )
        return _redirect_with_state(
            "core.upload_form",
            {"view": "upload", "error": str(error), "status_code": 500},
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
        current_app.logger.error("Accepted dataset could not be profiled: id=%s", dataset_id)
        return _redirect_with_state(
            "core.upload_form",
            {"view": "upload", "error": str(error), "status_code": 422},
        )

    return redirect(url_for("core.dataset_profile", dataset_id=dataset_id), code=303)


@core.get("/dataset/<dataset_id>")
def dataset_profile(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display a stable GET page for an uploaded dataset and transient UI results."""

    dataset_path = _dataset_path(dataset_id)
    if (
        dataset_path.suffix == ".xlsx"
        and load_xlsx_selection(Path(current_app.config["UPLOAD_DIR"]), dataset_id) is None
    ):
        return redirect(url_for("core.excel_sheet_selection", dataset_id=dataset_id), code=303)
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
        rejected_derived_suggestion_count=_state_count(state, "rejected_derived_suggestion_count"),
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
        save_xlsx_selection(Path(current_app.config["UPLOAD_DIR"]), dataset_id, table_name)
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
            tuple(metric.name for metric in existing.metrics) if existing is not None else ()
        )
        batch = generate_configuration_suggestions(
            profile,
            dataset_id=dataset_id,
            model=str(current_app.config["OLLAMA_MODEL"]),
            host=str(current_app.config["OLLAMA_HOST"]),
            timeout_seconds=int(current_app.config["OLLAMA_TIMEOUT_SECONDS"]),
            excluded_kpis=excluded_kpis,
            metrics_dir=Path(current_app.config["MODEL_RUN_METRICS_DIR"]),
        )
    except (BusinessConfigurationError, ConfigurationSuggestionError) as error:
        current_app.logger.warning(
            "Local configuration suggestions unavailable: id=%s model=%s reason=%s",
            dataset_id,
            current_app.config["OLLAMA_MODEL"],
            error,
        )
        return _redirect_profile_state(dataset_id, suggestion_error=str(error), status_code=503)

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
                target_or_benchmark=request.form.get("target_or_benchmark", ""),
                target_scope=request.form.get("target_scope", "row") or "row",
                business_objective=request.form.get("business_objective", ""),
                aggregation=request.form.get("aggregation", "sum") or "sum",
                display_format=(request.form.get("display_format", "number") or "number"),
            )
            form_data = request.form.copy()
            notice = "AI suggestion loaded. Review or edit every field before confirming."
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
                business_objective=request.form.get("business_objective", ""),
                target_or_benchmark=request.form.get("target_or_benchmark", ""),
                aggregation=request.form.get("aggregation", "sum") or "sum",
                display_format=(request.form.get("display_format", "number") or "number"),
                target_scope=request.form.get("target_scope", "row") or "row",
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
            metrics_dir=Path(current_app.config["MODEL_RUN_METRICS_DIR"]),
            dataset_id=dataset_id,
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
            status_code=(_state_status(state, default=400) if configuration_error else 400),
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


@core.get("/conditional/<dataset_id>")
def conditional_kpi_editor(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display the deterministic conditional-rate KPI builder."""

    profile = _load_profile(dataset_id)
    view = _load_dataset_view_for_id(dataset_id)
    state = _load_view_state("conditional", dataset_id=dataset_id)
    form_data = _form_from_state(state.get("form_data")) or MultiDict()
    existing = _load_existing_configuration(dataset_id, profile)
    if existing is not None:
        _populate_existing_business_fields(form_data, existing)
    return (
        render_template(
            "conditional_configuration.html",
            dataset_id=dataset_id,
            profile=profile,
            configuration=existing,
            form_data=form_data,
            configuration_error=_state_text(
                state,
                "configuration_error",
            ),
            condition_values=condition_value_options(
                view,
                profile,
            ),
            calculation_bases=("record_count", "value_sum"),
            target_scope_options=tuple(scope for scope in TARGET_SCOPES if scope != "row"),
        ),
        _state_status(state),
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
                target_or_benchmark=request.form.get("target_or_benchmark", ""),
                business_objective=request.form.get("business_objective", ""),
                secondary_kpis=request.form.getlist("secondary_kpis"),
                aggregation=request.form.get("aggregation", "sum") or "sum",
                display_format=(request.form.get("display_format", "number") or "number"),
                target_scope=request.form.get("target_scope", "row") or "row",
            )
        else:
            context_submitted = request.form.get("context_submitted", "") == "yes"
            configuration = add_source_metrics(
                profile,
                dataset_id=dataset_id,
                source_columns=request.form.getlist("source_kpis"),
                kpi_direction=request.form.get("kpi_direction", ""),
                existing_configuration=existing,
                date_column=(request.form.get("date_column", "") if context_submitted else None),
                category_columns=(
                    request.form.getlist("category_columns") if context_submitted else None
                ),
                business_objective=(
                    request.form.get("business_objective", "") if context_submitted else None
                ),
                target_or_benchmark=request.form.get(
                    "target_or_benchmark",
                    "",
                ),
                aggregation=request.form.get("aggregation", "sum") or "sum",
                display_format=(request.form.get("display_format", "number") or "number"),
                target_scope=request.form.get("target_scope", "row") or "row",
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
    return redirect(url_for("core.saved_configuration", dataset_id=dataset_id), code=303)


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
            target_scope=request.form.get("target_scope") or None,
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
    return redirect(url_for("core.saved_configuration", dataset_id=dataset_id), code=303)


@core.post("/configure-conditional/<dataset_id>")
def configure_conditional_kpi(
    dataset_id: str,
):  # type: ignore[no-untyped-def]
    """Validate and retain a conditional count/value percentage KPI."""

    profile = _load_profile(dataset_id)
    view = _load_dataset_view_for_id(dataset_id)
    condition_column = request.form.get("condition_column", "")
    try:
        metric = validate_conditional_metric(
            profile,
            view,
            name=request.form.get("name", ""),
            calculation_base=request.form.get(
                "calculation_base",
                "",
            ),
            condition_column=condition_column,
            included_values=request.form.getlist(f"included_values::{condition_column}"),
            value_column=request.form.get("value_column", ""),
            row_grain_confirmed=(request.form.get("row_grain_confirmed", "") == "yes"),
            source_id=source_id_from_hash(
                profile.source_sha256,
                profile.source_table_name,
            ),
        )
        configuration = validate_conditional_business_configuration(
            profile,
            dataset_id=dataset_id,
            conditional_metric=metric,
            kpi_direction=request.form.get("kpi_direction", ""),
            date_column=request.form.get("date_column", ""),
            category_columns=request.form.getlist("category_columns"),
            target_or_benchmark=request.form.get(
                "target_or_benchmark",
                "",
            ),
            business_objective=request.form.get(
                "business_objective",
                "",
            ),
            existing_configuration=_load_existing_configuration(
                dataset_id,
                profile,
            ),
            metric_role=request.form.get("metric_role", "secondary"),
            target_scope=(request.form.get("target_scope", "dataset") or "dataset"),
        )
        save_business_configuration(
            configuration,
            configuration_dir=Path(current_app.config["CONFIGURATION_DIR"]),
        )
    except (ConditionalMetricError, BusinessConfigurationError) as error:
        return _redirect_with_state(
            "core.conditional_kpi_editor",
            {
                "view": "conditional",
                "dataset_id": dataset_id,
                "form_data": _form_to_state(request.form),
                "configuration_error": str(error),
                "status_code": 400,
            },
            dataset_id=dataset_id,
        )
    return redirect(
        url_for("core.saved_configuration", dataset_id=dataset_id),
        code=303,
    )


@core.get("/configuration/<dataset_id>")
def saved_configuration(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display a retained configuration from a stable GET URL."""

    profile = _load_profile(dataset_id)
    configuration_path = Path(current_app.config["CONFIGURATION_DIR"]) / (f"{dataset_id}.json")
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
        configuration = set_primary_metric(configuration, request.form.get("metric_id", ""))
        save_business_configuration(
            configuration,
            configuration_dir=Path(current_app.config["CONFIGURATION_DIR"]),
        )
    except BusinessConfigurationError as error:
        return _redirect_configuration_error(dataset_id, str(error))
    return redirect(url_for("core.saved_configuration", dataset_id=dataset_id), code=303)


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
            aggregation=request.form.get("aggregation"),
            display_format=request.form.get("display_format"),
            target_scope=request.form.get("target_scope"),
        )
        save_business_configuration(
            configuration,
            configuration_dir=Path(current_app.config["CONFIGURATION_DIR"]),
        )
    except BusinessConfigurationError as error:
        return _redirect_configuration_error(dataset_id, str(error))
    return redirect(url_for("core.saved_configuration", dataset_id=dataset_id), code=303)


@core.post("/configuration/<dataset_id>/remove")
def remove_configured_metric(dataset_id: str):  # type: ignore[no-untyped-def]
    """Remove a non-primary KPI from the registry."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
        configuration = remove_metric(configuration, request.form.get("metric_id", ""))
        save_business_configuration(
            configuration,
            configuration_dir=Path(current_app.config["CONFIGURATION_DIR"]),
        )
    except BusinessConfigurationError as error:
        return _redirect_configuration_error(dataset_id, str(error))
    return redirect(url_for("core.saved_configuration", dataset_id=dataset_id), code=303)


# Report selection, generation, publication, and export


@core.get("/reports/<dataset_id>/configure")
def report_configuration_form(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display the deterministic Milestone 5A report selection form."""

    profile = _load_profile(dataset_id)
    try:
        configuration, evidence, visualizations, manual_boards = _report_assets(
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
                    manual_boards=manual_boards,
                )
                form_data = _report_form_from_configuration(saved)
            except ReportConfigurationError as error:
                stale_notice = str(error)
        if form_data is None:
            form_data = _default_report_form(
                configuration,
                evidence_payload=evidence,
                visualizations=visualizations,
                manual_boards=manual_boards,
            )
    evidence_records = _sorted_evidence_records(evidence)
    visualization_insights = {
        insight.visualization_id: insight
        for insight in _load_visualization_insights(
            (*visualizations, *manual_boards)
        )
    }
    return (
        render_template(
            "report_configuration_form.html",
            dataset_id=dataset_id,
            configuration=configuration,
            evidence_records=evidence_records,
            finding_evidence_records=tuple(
                record for record in evidence_records if _evidence_kind(record) == "finding"
            ),
            association_evidence_records=tuple(
                record for record in evidence_records if _evidence_kind(record) == "association"
            ),
            diagnostic_evidence_records=tuple(
                record for record in evidence_records if _evidence_kind(record) == "diagnostic"
            ),
            recommended_evidence_ids=frozenset(
                _recommended_evidence_ids(
                    configuration,
                    evidence,
                )
            ),
            visualizations=visualizations,
            manual_boards=manual_boards,
            visualization_insights=visualization_insights,
            visualization_metric_requirements={
                artifact.visualization_id: tuple(
                    measure.selector.removeprefix("metric:")
                    for measure in artifact.measures
                    if measure.selector.startswith("metric:")
                )
                for artifact in visualizations
                if artifact.visualization_id is not None
            },
            metric_names={metric.metric_id: metric.name for metric in configuration.metrics},
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
        configuration, evidence, visualizations, manual_boards = _report_assets(
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
            business_objective=request.form.get("business_objective", ""),
            audience=request.form.get("audience", ""),
            tone=request.form.get("tone", ""),
            detail_level=request.form.get("detail_level", ""),
            user_notes=request.form.get("user_notes", ""),
            include_evidence_appendix=request.form.get("include_evidence_appendix", ""),
            selected_metric_ids=request.form.getlist("selected_metric_ids"),
            selected_evidence_ids=request.form.getlist("selected_evidence_ids"),
            selected_visualization_ids=request.form.getlist("selected_visualization_ids"),
            manual_boards=manual_boards,
            selected_manual_board_ids=request.form.getlist("selected_manual_board_ids"),
        )
        package = build_report_generation_package(
            report,
            configuration=configuration,
            evidence_payload=evidence,
            visualizations=visualizations,
            manual_boards=manual_boards,
            visualization_insights=_load_visualization_insights(
                (*visualizations, *manual_boards)
            ),
        )
        save_report_configuration(
            report,
            report_configuration_dir=Path(current_app.config["REPORT_CONFIGURATION_DIR"]),
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
        configuration, evidence, visualizations, manual_boards = _report_assets(
            dataset_id,
            profile,
        )
        report = load_report_configuration(
            path,
            configuration=configuration,
            evidence_payload=evidence,
            visualizations=visualizations,
            manual_boards=manual_boards,
        )
        package = build_report_generation_package(
            report,
            configuration=configuration,
            evidence_payload=evidence,
            visualizations=visualizations,
            manual_boards=manual_boards,
            visualization_insights=_load_visualization_insights(
                (*visualizations, *manual_boards)
            ),
        )
    except (
        BusinessConfigurationError,
        EvidenceError,
        ReportConfigurationError,
        ReportGenerationPackageError,
        VisualizationError,
    ) as error:
        abort(422, description=str(error))
    metric_by_id = {metric.metric_id: metric for metric in configuration.metrics}
    evidence_by_id = {
        str(record.get("id")): record for record in _sorted_evidence_records(evidence)
    }
    visualization_by_id = {
        artifact.visualization_id: artifact
        for artifact in visualizations
        if artifact.visualization_id is not None
    }
    manual_board_by_id = {
        artifact.visualization_id: artifact for artifact in manual_boards
    }
    state = _load_view_state(
        "saved_report_configuration",
        dataset_id=dataset_id,
    )
    try:
        latest_report = latest_generated_report(
            dataset_id,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
            expected_package_sha256=artifact_sha256(package.to_dict()),
        )
    except ReportNarrationError:
        latest_report = None
    return (
        render_template(
            "report_configuration.html",
            report=report,
            selected_metrics=tuple(
                metric_by_id[metric_id] for metric_id in report.selected_metric_ids
            ),
            selected_evidence=tuple(
                evidence_by_id[evidence_id] for evidence_id in report.selected_evidence_ids
            ),
            selected_visualizations=tuple(
                visualization_by_id[visualization_id]
                for visualization_id in report.selected_visualization_ids
            ),
            selected_manual_boards=tuple(
                manual_board_by_id[visualization_id]
                for visualization_id in report.selected_manual_board_ids
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
        configuration, evidence, visualizations, manual_boards = _report_assets(
            dataset_id,
            profile,
        )
        report = load_report_configuration(
            path,
            configuration=configuration,
            evidence_payload=evidence,
            visualizations=visualizations,
            manual_boards=manual_boards,
        )
        package = build_report_generation_package(
            report,
            configuration=configuration,
            evidence_payload=evidence,
            visualizations=visualizations,
            manual_boards=manual_boards,
            visualization_insights=_load_visualization_insights(
                (*visualizations, *manual_boards)
            ),
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
        _configuration, _evidence, visualizations, manual_boards, package = _current_report_package(
            dataset_id, profile
        )
        draft = generate_narrated_report(
            package,
            model=str(current_app.config["OLLAMA_MODEL"]),
            host=str(current_app.config["OLLAMA_HOST"]),
            timeout_seconds=int(current_app.config["OLLAMA_TIMEOUT_SECONDS"]),
            temperature=float(current_app.config["OLLAMA_REPORT_TEMPERATURE"]),
            metrics_dir=Path(current_app.config["MODEL_RUN_METRICS_DIR"]),
        )
        if (
            draft.stories
            and not draft.ai_narrated_evidence_ids
            and draft.generation_diagnostics.get("rejected_story_ids")
        ):
            raise ReportNarrationError(
                "Ollama returned responses, but none passed evidence "
                "validation after four attempts per story. No report was "
                "saved. Try Generate report again; if this persists, use a "
                "more capable local model."
            )
        generated, _path = _save_generated_report_with_charts(
            draft,
            visualizations=visualizations,
            manual_boards=manual_boards,
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
            "Evidence-grounded report generation unavailable: id=%s model=%s reason=%s",
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


@core.get("/reports/<dataset_id>/history")
def generated_report_history(dataset_id: str):  # type: ignore[no-untyped-def]
    """List every immutable report version retained for one dataset."""

    try:
        reports = list_generated_report_versions(
            dataset_id,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
        )
    except ReportNarrationError as error:
        abort(422, description=str(error))
    try:
        workspace_record = load_workspace_record(
            dataset_id,
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
        )
    except WorkspaceHistoryError as error:
        abort(422, description=str(error))
    archived_ids = (
        set(workspace_record.archived_report_ids) if workspace_record is not None else set()
    )
    active_reports = tuple(report for report in reports if report.report_id not in archived_ids)
    archived_reports = tuple(report for report in reports if report.report_id in archived_ids)
    current_package_sha256 = _current_package_sha256(dataset_id)
    return render_template(
        "report_history.html",
        dataset_id=dataset_id,
        reports=active_reports,
        archived_reports=archived_reports,
        report_names=(workspace_record.report_names if workspace_record is not None else {}),
        workspace_is_archived=(
            workspace_record.is_archived if workspace_record is not None else False
        ),
        current_package_sha256=current_package_sha256,
        configuration=_configuration_path(dataset_id).is_file(),
        report_run_count=len({report.report_id for report in active_reports}),
    )


@core.get("/reports/<dataset_id>/generated")
def latest_report(dataset_id: str):  # type: ignore[no-untyped-def]
    """Open the latest generated report bound to the current package."""

    profile = _load_profile(dataset_id)
    try:
        (
            _configuration,
            _evidence,
            _visualizations,
            _manual_boards,
            package,
        ) = _current_report_package(dataset_id, profile)
        report = latest_generated_report(
            dataset_id,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
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
    _ensure_report_active(dataset_id, report.report_id)
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

    _ensure_report_active(dataset_id, report_id)
    profile = _load_profile(dataset_id)
    try:
        (
            _configuration,
            _evidence,
            _visualizations,
            _manual_boards,
            package,
        ) = _current_report_package(dataset_id, profile)
        report = load_generated_report(
            dataset_id,
            report_id,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
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
    return _render_generated_report(
        report,
        historical_snapshot=False,
        report_pdf_url=url_for(
            "core.generated_report_pdf",
            dataset_id=dataset_id,
            report_id=report_id,
        ),
        report_json_url=url_for(
            "core.generated_report_json",
            dataset_id=dataset_id,
            report_id=report_id,
        ),
    )


@core.get("/reports/<dataset_id>/generated/<report_id>/versions/<int:version>")
def generated_report_version(
    dataset_id: str,
    report_id: str,
    version: int,
):  # type: ignore[no-untyped-def]
    """Render one exact historical report snapshot without mutating it."""

    _ensure_report_active(dataset_id, report_id)
    try:
        report = load_generated_report_version(
            dataset_id,
            report_id,
            version,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
        )
    except ReportNarrationError as error:
        abort(422, description=str(error))
    return _render_generated_report(
        report,
        historical_snapshot=True,
        report_pdf_url=url_for(
            "core.generated_report_version_pdf",
            dataset_id=dataset_id,
            report_id=report_id,
            version=version,
        ),
        report_json_url=url_for(
            "core.generated_report_version_json",
            dataset_id=dataset_id,
            report_id=report_id,
            version=version,
        ),
    )


@core.post("/reports/<dataset_id>/generated/<report_id>/presentation")
def publish_generated_report(
    dataset_id: str,
    report_id: str,
):  # type: ignore[no-untyped-def]
    """Save story inclusion and ordering as a new immutable report version."""

    _ensure_report_active(dataset_id, report_id, mutable=True)
    profile = _load_profile(dataset_id)
    try:
        (
            _configuration,
            _evidence,
            _visualizations,
            _manual_boards,
            package,
        ) = _current_report_package(dataset_id, profile)
        report = load_generated_report(
            dataset_id,
            report_id,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
            expected_package_sha256=artifact_sha256(package.to_dict()),
        )
        included_story_ids = tuple(request.form.getlist("included_story_ids"))
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
        if any(position < 1 for position, _story_id in positions) or len(
            {position for position, _story_id in positions}
        ) != len(positions):
            raise ReportNarrationError(
                "Report story display positions must be unique positive numbers."
            )
        revised = publish_report_presentation(
            report,
            included_story_ids=included_story_ids,
            story_order=tuple(story_id for _position, story_id in sorted(positions)),
        )
        published, _path = _save_generated_report_with_charts(
            revised,
            visualizations=_visualizations,
            manual_boards=_manual_boards,
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


@core.post("/reports/<dataset_id>/generated/<report_id>/stories/<story_id>/regenerate")
def regenerate_report_story(
    dataset_id: str,
    report_id: str,
    story_id: str,
):  # type: ignore[no-untyped-def]
    """Regenerate only one story and append a new immutable version."""

    _ensure_report_active(dataset_id, report_id, mutable=True)
    profile = _load_profile(dataset_id)
    try:
        (
            _configuration,
            _evidence,
            _visualizations,
            _manual_boards,
            package,
        ) = _current_report_package(dataset_id, profile)
        report = load_generated_report(
            dataset_id,
            report_id,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
            expected_package_sha256=artifact_sha256(package.to_dict()),
        )
        revised = regenerate_generated_story(
            report,
            package,
            story_id=story_id,
            model=str(current_app.config["OLLAMA_MODEL"]),
            host=str(current_app.config["OLLAMA_HOST"]),
            timeout_seconds=int(current_app.config["OLLAMA_TIMEOUT_SECONDS"]),
            temperature=float(current_app.config["OLLAMA_REPORT_TEMPERATURE"]),
            metrics_dir=Path(current_app.config["MODEL_RUN_METRICS_DIR"]),
        )
        regenerated, _path = _save_generated_report_with_charts(
            revised,
            visualizations=_visualizations,
            manual_boards=_manual_boards,
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

    _ensure_report_active(dataset_id, report_id)
    profile = _load_profile(dataset_id)
    try:
        _configuration, _evidence, visualizations, manual_boards, package = _current_report_package(
            dataset_id, profile
        )
        report = load_generated_report(
            dataset_id,
            report_id,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
            expected_package_sha256=artifact_sha256(package.to_dict()),
        )
        rendered = build_report_pdf(
            report,
            chart_paths=_generated_report_chart_paths(
                report,
                visualizations=visualizations,
                manual_boards=manual_boards,
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

    _ensure_report_active(dataset_id, report_id)
    profile = _load_profile(dataset_id)
    try:
        (
            _configuration,
            _evidence,
            _visualizations,
            _manual_boards,
            package,
        ) = _current_report_package(dataset_id, profile)
        report = load_generated_report(
            dataset_id,
            report_id,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
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


@core.get("/reports/<dataset_id>/generated/<report_id>/versions/<int:version>/pdf")
def generated_report_version_pdf(
    dataset_id: str,
    report_id: str,
    version: int,
):  # type: ignore[no-untyped-def]
    """Download a PDF rendered from one exact historical report version."""

    _ensure_report_active(dataset_id, report_id)
    try:
        report = load_generated_report_version(
            dataset_id,
            report_id,
            version,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
        )
        rendered = build_report_pdf(
            report,
            chart_paths=_historical_report_chart_paths(report),
        )
    except (ReportNarrationError, ReportPdfError) as error:
        abort(422, description=str(error))
    return send_file(
        io.BytesIO(rendered),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=report_pdf_filename(report),
        max_age=0,
    )


@core.get("/reports/<dataset_id>/generated/<report_id>/versions/<int:version>/json")
def generated_report_version_json(
    dataset_id: str,
    report_id: str,
    version: int,
):  # type: ignore[no-untyped-def]
    """Return one exact historical generated-report artifact as JSON."""

    _ensure_report_active(dataset_id, report_id)
    try:
        report = load_generated_report_version(
            dataset_id,
            report_id,
            version,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
        )
    except ReportNarrationError as error:
        abort(422, description=str(error))
    return jsonify(report.to_dict())


@core.get("/reports/<dataset_id>/generated/<report_id>/versions/<int:version>/charts/<evidence_id>")
def generated_report_version_chart(
    dataset_id: str,
    report_id: str,
    version: int,
    evidence_id: str,
):  # type: ignore[no-untyped-def]
    """Serve a still-retained chart referenced by one historical report."""

    _ensure_report_active(dataset_id, report_id)
    try:
        report = load_generated_report_version(
            dataset_id,
            report_id,
            version,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
        )
    except ReportNarrationError as error:
        abort(422, description=str(error))
    chart_paths = _historical_report_chart_paths(report)
    path = chart_paths.get(evidence_id)
    if path is None:
        abort(404)
    return send_from_directory(
        path.parent,
        path.name,
        mimetype="image/png",
        max_age=0,
    )


# Visualization workflows


@core.get("/workspaces/<dataset_id>/dashboard")
@core.get("/visualizations/<dataset_id>")
def saved_visualizations(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display the report dashboard and its saved visualizations."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
        visualizations = list_visualizations(
            dataset_id=dataset_id,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
            profile=profile,
            configuration=configuration,
        )
        manual_visualizations = list_manual_visualizations(
            dataset_id=dataset_id,
            source_sha256=profile.source_sha256,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
        )
    except (
        BusinessConfigurationError,
        ManualVisualizationStoreError,
        VisualizationError,
    ) as error:
        abort(422, description=str(error))
    return render_template(
        "visualizations.html",
        dataset_id=dataset_id,
        configuration=configuration,
        visualizations=visualizations,
        manual_visualizations=manual_visualizations,
        evidence_ready=(Path(current_app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json").is_file(),
        report_configuration_ready=_report_configuration_path(dataset_id).is_file(),
    )


@core.get("/visualizations/<dataset_id>/new")
def visualization_builder(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display the existing automated visualization builder."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
    except BusinessConfigurationError as error:
        abort(422, description=str(error))
    state = _load_view_state("visualization_builder", dataset_id=dataset_id)
    state_form = _form_from_state(state.get("form_data"))
    form_data = state_form or _default_visualization_form(
        profile,
        configuration=configuration,
    )
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
            assistant_request=_state_text(state, "assistant_request"),
            suggestion_model=current_app.config["OLLAMA_MODEL"],
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


@core.get("/visualizations/<dataset_id>/build")
def visualization_builder_choice(dataset_id: str):  # type: ignore[no-untyped-def]
    """Let generic visualization actions choose the appropriate builder."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
    except BusinessConfigurationError as error:
        abort(422, description=str(error))
    return render_template(
        "visualization_builder_choice.html",
        dataset_id=dataset_id,
        configuration=configuration,
    )


@core.get("/visualizations/<dataset_id>/manual/new")
def manual_visualization_builder(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display the separate manual visualization workspace."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
    except BusinessConfigurationError as error:
        abort(422, description=str(error))
    initial_state = None
    edit_id = request.args.get("edit", "")
    if edit_id:
        try:
            artifact = load_manual_visualization(
                edit_id,
                dataset_id=dataset_id,
                source_sha256=profile.source_sha256,
                visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
            )
        except ManualVisualizationStoreError as error:
            abort(404, description=str(error))
        initial_state = {
            "visualization_id": artifact.visualization_id,
            "title": artifact.title,
            "chart": artifact.requested_chart,
            "fields": artifact.fields,
            "settings": artifact.settings,
        }
    response = current_app.make_response(
        render_template(
            "manual_visualization_builder.html",
            dataset_id=dataset_id,
            configuration=configuration,
            profile=profile,
            manual_initial_state=initial_state,
        )
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' blob: data:"
    )
    return response


@core.get("/visualizations/<dataset_id>/manual/preview-data")
def manual_visualization_preview_data(dataset_id: str):  # type: ignore[no-untyped-def]
    """Return bounded data for the manual builder's live browser preview."""

    profile = _load_profile(dataset_id)
    try:
        payload = build_manual_visualization_preview(
            _load_dataset_view_for_id(dataset_id),
            profile,
            x_column=request.args.get("x") or None,
            y_column=request.args.get("y") or None,
            series_column=request.args.get("series") or None,
            size_column=request.args.get("size") or None,
            secondary_y_column=request.args.get("secondary_y") or None,
            target_value=request.args.get("target") or None,
            requested_chart=request.args.get("chart", "auto"),
        )
    except (DatasetViewError, ManualVisualizationPreviewError) as error:
        return jsonify(error=str(error)), 422
    return jsonify(payload)


@core.post("/visualizations/<dataset_id>/manual/save")
def save_manual_visualization_board(dataset_id: str):  # type: ignore[no-untyped-def]
    """Persist the current manual-board state and its safe SVG snapshot."""

    values = request.get_json(silent=True)
    if not isinstance(values, dict):
        return jsonify(error="A JSON visualization payload is required."), 400
    fields = values.get("fields")
    settings = values.get("settings")
    if not isinstance(fields, dict) or not isinstance(settings, dict):
        return jsonify(error="Visualization fields and settings are required."), 400
    profile = _load_profile(dataset_id)

    def field(role: str) -> str | None:
        value = fields.get(role)
        return value if isinstance(value, str) and value else None

    target = settings.get("target")
    target_text = str(target) if isinstance(target, (int, float)) else None
    try:
        preview = build_manual_visualization_preview(
            _load_dataset_view_for_id(dataset_id),
            profile,
            x_column=field("x"),
            y_column=field("y"),
            series_column=field("series"),
            size_column=field("size"),
            secondary_y_column=field("secondary_y"),
            target_value=target_text,
            requested_chart=str(values.get("chart", "auto")),
        )
        artifact = save_manual_visualization(
            dataset_id=dataset_id,
            source_sha256=profile.source_sha256,
            values=values,
            preview=preview,
            svg_markup=str(values.get("svg", "")),
            png_data_url=(values.get("png") if isinstance(values.get("png"), str) else None),
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
        )
    except (
        DatasetViewError,
        ManualVisualizationPreviewError,
        ManualVisualizationStoreError,
    ) as error:
        return jsonify(error=str(error)), 422
    return (
        jsonify(
            visualization_id=artifact.visualization_id,
            url=url_for(
                "core.saved_manual_visualization",
                dataset_id=dataset_id,
                visualization_id=artifact.visualization_id,
            ),
        ),
        200 if values.get("visualization_id") else 201,
    )


@core.get("/visualizations/<dataset_id>/manual/<visualization_id>")
def saved_manual_visualization(dataset_id: str, visualization_id: str):  # type: ignore[no-untyped-def]
    """Display one saved interactive manual visualization."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
        artifact = load_manual_visualization(
            visualization_id,
            dataset_id=dataset_id,
            source_sha256=profile.source_sha256,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
        )
    except (BusinessConfigurationError, ManualVisualizationStoreError) as error:
        abort(404, description=str(error))
    insight = load_visualization_insight(
        artifact,
        insight_dir=Path(current_app.config["VISUALIZATION_INSIGHT_DIR"]),
    )
    state = _load_view_state(
        "saved_manual_visualization",
        dataset_id=dataset_id,
    )
    return render_template(
        "manual_visualization.html",
        dataset_id=dataset_id,
        configuration=configuration,
        artifact=artifact,
        visualization_insight=insight,
        insight_notice=_state_text(state, "insight_notice"),
        insight_error=_state_text(state, "insight_error"),
    )


@core.post("/visualizations/<dataset_id>/manual/<visualization_id>/insights")
def request_manual_visualization_insight(
    dataset_id: str,
    visualization_id: str,
):  # type: ignore[no-untyped-def]
    """Generate grounded findings for one saved manual-board visualization."""

    profile = _load_profile(dataset_id)
    try:
        artifact = load_manual_visualization(
            visualization_id,
            dataset_id=dataset_id,
            source_sha256=profile.source_sha256,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
        )
        insight = generate_visualization_insight(
            artifact,
            question=request.form.get("question", ""),
            include_in_reports=(request.form.get("include_in_reports") == "yes"),
            use_model=request.form.get("use_model") == "yes",
            model=str(current_app.config["OLLAMA_MODEL"]),
            host=str(current_app.config["OLLAMA_HOST"]),
            timeout_seconds=int(current_app.config["OLLAMA_TIMEOUT_SECONDS"]),
            metrics_dir=Path(current_app.config["MODEL_RUN_METRICS_DIR"]),
        )
        save_visualization_insight(
            insight,
            insight_dir=Path(current_app.config["VISUALIZATION_INSIGHT_DIR"]),
        )
    except (ManualVisualizationStoreError, VisualizationInsightError) as error:
        return _redirect_with_state(
            "core.saved_manual_visualization",
            {
                "view": "saved_manual_visualization",
                "dataset_id": dataset_id,
                "insight_error": str(error),
                "status_code": 400,
            },
            dataset_id=dataset_id,
            visualization_id=visualization_id,
        )
    notice = (
        "Five grounded chart findings were saved."
        if len(insight.points) == 5
        else f"{len(insight.points)} grounded chart findings were saved."
    )
    if insight.model_status == "unavailable":
        notice += (
            " Ollama was unavailable or returned unsafe text, so the "
            "Python-derived findings were retained without AI interpretation."
        )
    return _redirect_with_state(
        "core.saved_manual_visualization",
        {
            "view": "saved_manual_visualization",
            "dataset_id": dataset_id,
            "insight_notice": notice,
        },
        dataset_id=dataset_id,
        visualization_id=visualization_id,
    )


@core.post(
    "/visualizations/<dataset_id>/manual/<visualization_id>/insights/report-inclusion"
)
def update_manual_visualization_insight_report_inclusion(
    dataset_id: str,
    visualization_id: str,
):  # type: ignore[no-untyped-def]
    """Change whether a manual-board insight follows its chart into reports."""

    profile = _load_profile(dataset_id)
    try:
        artifact = load_manual_visualization(
            visualization_id,
            dataset_id=dataset_id,
            source_sha256=profile.source_sha256,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
        )
        insight = load_visualization_insight(
            artifact,
            insight_dir=Path(current_app.config["VISUALIZATION_INSIGHT_DIR"]),
        )
        if insight is None:
            raise VisualizationInsightError(
                "Generate chart insights before changing report inclusion."
            )
        updated = set_visualization_insight_report_inclusion(
            insight,
            include_in_reports=(request.form.get("include_in_reports") == "yes"),
        )
        save_visualization_insight(
            updated,
            insight_dir=Path(current_app.config["VISUALIZATION_INSIGHT_DIR"]),
        )
    except (ManualVisualizationStoreError, VisualizationInsightError) as error:
        return _redirect_with_state(
            "core.saved_manual_visualization",
            {
                "view": "saved_manual_visualization",
                "dataset_id": dataset_id,
                "insight_error": str(error),
                "status_code": 400,
            },
            dataset_id=dataset_id,
            visualization_id=visualization_id,
        )
    notice = (
        "Chart insights will be included when this visualization is selected for a report."
        if updated.include_in_reports
        else "Chart insights will remain saved but will not be included in reports."
    )
    return _redirect_with_state(
        "core.saved_manual_visualization",
        {
            "view": "saved_manual_visualization",
            "dataset_id": dataset_id,
            "insight_notice": notice,
        },
        dataset_id=dataset_id,
        visualization_id=visualization_id,
    )


@core.get("/visualizations/<dataset_id>/manual/<visualization_id>/chart")
def saved_manual_visualization_chart(dataset_id: str, visualization_id: str):  # type: ignore[no-untyped-def]
    """Serve a sanitized saved SVG through its validated artifact."""

    profile = _load_profile(dataset_id)
    try:
        artifact = load_manual_visualization(
            visualization_id,
            dataset_id=dataset_id,
            source_sha256=profile.source_sha256,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
        )
        path = manual_visualization_svg_path(
            artifact,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
        )
    except ManualVisualizationStoreError as error:
        abort(404, description=str(error))
    response = send_file(path, mimetype="image/svg+xml", max_age=0)
    response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return response


@core.get("/visualizations/<dataset_id>/manual/<visualization_id>/chart.png")
def saved_manual_visualization_chart_png(
    dataset_id: str,
    visualization_id: str,
):  # type: ignore[no-untyped-def]
    """Serve the validated PNG retained for dashboard and report rendering."""

    profile = _load_profile(dataset_id)
    try:
        artifact = load_manual_visualization(
            visualization_id,
            dataset_id=dataset_id,
            source_sha256=profile.source_sha256,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
        )
        path = manual_visualization_png_path(
            artifact,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
        )
    except ManualVisualizationStoreError as error:
        abort(404, description=str(error))
    if path is None:
        abort(404, description="Manual visualization PNG was not found.")
    return send_file(path, mimetype="image/png", max_age=0)


@core.post("/visualizations/<dataset_id>/assistant")
def suggest_visualization(dataset_id: str):  # type: ignore[no-untyped-def]
    """Ask Ollama for one chart, then build a validated preview with Python."""

    profile = _load_profile(dataset_id)
    user_request = request.form.get("user_request", "")
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
        suggestion = generate_visualization_suggestion(
            profile,
            configuration=configuration,
            user_request=user_request,
            dataset_id=dataset_id,
            model=str(current_app.config["OLLAMA_MODEL"]),
            host=str(current_app.config["OLLAMA_HOST"]),
            timeout_seconds=int(current_app.config["OLLAMA_TIMEOUT_SECONDS"]),
            metrics_dir=Path(current_app.config["MODEL_RUN_METRICS_DIR"]),
        )
        artifact = build_visualization(
            _load_dataset_view_for_id(dataset_id),
            profile=profile,
            configuration=configuration,
            spec=suggestion.spec,
            chart_dir=Path(current_app.config["CHART_DIR"]),
            assistant_metadata=suggestion.assistant_metadata(
                model=str(current_app.config["OLLAMA_MODEL"])
            ),
            dataset_id=dataset_id,
        )
        token = save_preview(
            artifact,
            preview_dir=Path(current_app.config["VISUALIZATION_PREVIEW_DIR"]),
            chart_dir=Path(current_app.config["CHART_DIR"]),
        )
    except (
        BusinessConfigurationError,
        VisualizationError,
        VisualizationSuggestionError,
    ) as error:
        return _redirect_with_state(
            "core.visualization_builder",
            {
                "view": "visualization_builder",
                "dataset_id": dataset_id,
                "assistant_request": user_request,
                "error": str(error),
                "status_code": 503 if isinstance(error, VisualizationSuggestionError) else 400,
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


@core.post("/visualizations/<dataset_id>/preview")
def preview_visualization(dataset_id: str):  # type: ignore[no-untyped-def]
    """Validate and generate a short-lived visualization preview."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
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
            dataset_id=dataset_id,
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
        configuration = _load_existing_configuration(dataset_id, profile)
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
        configuration=configuration,
    )


@core.get("/visualizations/<dataset_id>/preview/<token>/chart")
def visualization_preview_chart(dataset_id: str, token: str):  # type: ignore[no-untyped-def]
    """Serve a draft chart only through its validated preview token."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
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
        configuration = _load_existing_configuration(dataset_id, profile)
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
def saved_visualization(dataset_id: str, visualization_id: str):  # type: ignore[no-untyped-def]
    """Display a saved visualization and its reproducible supporting data."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
        artifact = load_visualization(
            visualization_id,
            dataset_id=dataset_id,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
            profile=profile,
            configuration=configuration,
        )
    except (BusinessConfigurationError, VisualizationError) as error:
        abort(404, description=str(error))
    insight = load_visualization_insight(
        artifact,
        insight_dir=Path(current_app.config["VISUALIZATION_INSIGHT_DIR"]),
    )
    state = _load_view_state(
        "saved_visualization",
        dataset_id=dataset_id,
    )
    return _render_visualization(
        artifact,
        artifact_json=json.dumps(artifact.to_dict(), indent=2, sort_keys=True),
        configuration=configuration,
        visualization_insight=insight,
        insight_notice=_state_text(state, "insight_notice"),
        insight_error=_state_text(state, "insight_error"),
    )


@core.post("/visualizations/<dataset_id>/<visualization_id>/insights")
def request_visualization_insight(dataset_id: str, visualization_id: str):  # type: ignore[no-untyped-def]
    """Derive chart facts and optionally add a grounded Ollama interpretation."""

    profile = _load_profile(dataset_id)
    question = request.form.get("question", "")
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
        artifact = load_visualization(
            visualization_id,
            dataset_id=dataset_id,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
            profile=profile,
            configuration=configuration,
        )
        insight = generate_visualization_insight(
            artifact,
            question=question,
            include_in_reports=(request.form.get("include_in_reports") == "yes"),
            use_model=request.form.get("use_model") == "yes",
            model=str(current_app.config["OLLAMA_MODEL"]),
            host=str(current_app.config["OLLAMA_HOST"]),
            timeout_seconds=int(current_app.config["OLLAMA_TIMEOUT_SECONDS"]),
            metrics_dir=Path(current_app.config["MODEL_RUN_METRICS_DIR"]),
        )
        save_visualization_insight(
            insight,
            insight_dir=Path(current_app.config["VISUALIZATION_INSIGHT_DIR"]),
        )
    except (
        BusinessConfigurationError,
        VisualizationError,
        VisualizationInsightError,
    ) as error:
        return _redirect_with_state(
            "core.saved_visualization",
            {
                "view": "saved_visualization",
                "dataset_id": dataset_id,
                "insight_error": str(error),
                "status_code": 400,
            },
            dataset_id=dataset_id,
            visualization_id=visualization_id,
        )
    notice = (
        "Five grounded chart findings were saved."
        if len(insight.points) == 5
        else f"{len(insight.points)} grounded chart findings were saved."
    )
    if insight.model_status == "unavailable":
        notice += (
            " Ollama was unavailable or returned unsafe text, so the "
            "Python-derived findings were retained without AI interpretation."
        )
    return _redirect_with_state(
        "core.saved_visualization",
        {
            "view": "saved_visualization",
            "dataset_id": dataset_id,
            "insight_notice": notice,
        },
        dataset_id=dataset_id,
        visualization_id=visualization_id,
    )


@core.post("/visualizations/<dataset_id>/<visualization_id>/insights/report-inclusion")
def update_visualization_insight_report_inclusion(dataset_id: str, visualization_id: str):  # type: ignore[no-untyped-def]
    """Change whether a saved chart insight follows the chart into reports."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
        artifact = load_visualization(
            visualization_id,
            dataset_id=dataset_id,
            visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
            profile=profile,
            configuration=configuration,
        )
        insight = load_visualization_insight(
            artifact,
            insight_dir=Path(current_app.config["VISUALIZATION_INSIGHT_DIR"]),
        )
        if insight is None:
            raise VisualizationInsightError(
                "Generate chart insights before changing report inclusion."
            )
        updated = set_visualization_insight_report_inclusion(
            insight,
            include_in_reports=(request.form.get("include_in_reports") == "yes"),
        )
        save_visualization_insight(
            updated,
            insight_dir=Path(current_app.config["VISUALIZATION_INSIGHT_DIR"]),
        )
    except (
        BusinessConfigurationError,
        VisualizationError,
        VisualizationInsightError,
    ) as error:
        return _redirect_with_state(
            "core.saved_visualization",
            {
                "view": "saved_visualization",
                "dataset_id": dataset_id,
                "insight_error": str(error),
                "status_code": 400,
            },
            dataset_id=dataset_id,
            visualization_id=visualization_id,
        )
    notice = (
        "Chart insights will be included when this visualization is selected for a report."
        if updated.include_in_reports
        else "Chart insights will remain saved but will not be included in reports."
    )
    return _redirect_with_state(
        "core.saved_visualization",
        {
            "view": "saved_visualization",
            "dataset_id": dataset_id,
            "insight_notice": notice,
        },
        dataset_id=dataset_id,
        visualization_id=visualization_id,
    )


@core.get("/visualizations/<dataset_id>/<visualization_id>/chart")
def saved_visualization_chart(dataset_id: str, visualization_id: str):  # type: ignore[no-untyped-def]
    """Serve a saved chart only through its validated visualization record."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
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
def regenerate_visualization(dataset_id: str, visualization_id: str):  # type: ignore[no-untyped-def]
    """Recalculate a saved visualization from its retained source and specification."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _load_existing_configuration(dataset_id, profile)
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
            dataset_id=dataset_id,
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


# Deterministic insights and evidence


@core.post("/insights/<dataset_id>")
def deterministic_insights(dataset_id: str):  # type: ignore[no-untyped-def]
    """Generate factual observations, evidence records, and charts using Python."""

    profile = _load_profile(dataset_id)
    configuration_path = Path(current_app.config["CONFIGURATION_DIR"]) / (f"{dataset_id}.json")
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
    return redirect(url_for("core.saved_insights", dataset_id=dataset_id), code=303)


@core.get("/insights/<dataset_id>")
def saved_insights(dataset_id: str):  # type: ignore[no-untyped-def]
    """Display retained deterministic evidence from a stable GET URL."""

    profile = _load_profile(dataset_id)
    try:
        configuration = _require_configuration(dataset_id, profile)
    except BusinessConfigurationError as error:
        abort(422, description=str(error))
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
            json.dumps(evidence, indent=2, sort_keys=True) if evidence is not None else None
        ),
        diagnostic_evidence_types=_DIAGNOSTIC_EVIDENCE_TYPES,
        association_evidence_types=_ASSOCIATION_EVIDENCE_TYPES,
        configuration=configuration,
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


# Shared request and rendering helpers


def _workspace_directories() -> WorkspaceDirectories:
    return WorkspaceDirectories(
        upload_dir=Path(current_app.config["UPLOAD_DIR"]),
        workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
        configuration_dir=Path(current_app.config["CONFIGURATION_DIR"]),
        insight_dir=Path(current_app.config["INSIGHT_DIR"]),
        evidence_dir=Path(current_app.config["EVIDENCE_DIR"]),
        visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
        visualization_insight_dir=Path(current_app.config["VISUALIZATION_INSIGHT_DIR"]),
        report_configuration_dir=Path(current_app.config["REPORT_CONFIGURATION_DIR"]),
        report_package_dir=Path(current_app.config["REPORT_PACKAGE_DIR"]),
        generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
        trash_dir=Path(current_app.config["TRASH_DIR"]),
    )


def _workspace_resume_url(workspace: WorkspaceSummary) -> str:
    dataset_id = workspace.record.dataset_id
    if workspace.stage in {"source_required", "source_archived"}:
        return url_for(
            "core.workspace_source_form",
            dataset_id=dataset_id,
        )
    if workspace.stage == "archived":
        return url_for(
            "core.workspace_detail",
            dataset_id=dataset_id,
        )
    endpoint_by_stage = {
        "report_generated": "core.generated_report_history",
        "report_configured": "core.saved_report_configuration",
        "insights_generated": "core.saved_insights",
        "kpis_configured": "core.saved_configuration",
        "uploaded": "core.dataset_profile",
    }
    return url_for(
        endpoint_by_stage[workspace.stage],
        dataset_id=dataset_id,
    )


def _workspace_create_report_url(
    workspace: WorkspaceSummary,
) -> str | None:
    if not workspace.record.has_source or workspace.record.is_archived:
        return None
    if workspace.stage in {"insights_generated", "report_generated"}:
        return url_for(
            "core.report_configuration_form",
            dataset_id=workspace.record.dataset_id,
        )
    if workspace.stage == "report_configured":
        return url_for(
            "core.saved_report_configuration",
            dataset_id=workspace.record.dataset_id,
        )
    return _workspace_resume_url(workspace)


def _required_workspace_summary(dataset_id: str) -> WorkspaceSummary:
    try:
        workspace = get_workspace_summary(
            dataset_id,
            directories=_workspace_directories(),
        )
    except WorkspaceHistoryError as error:
        abort(422, description=str(error))
    if workspace is None:
        abort(404)
    return workspace


def _materialize_workspace_metadata(dataset_id: str) -> None:
    """Adopt a source-only legacy workspace before a lifecycle mutation."""

    workspace = _required_workspace_summary(dataset_id)
    try:
        record = load_workspace_record(
            dataset_id,
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
        )
    except WorkspaceHistoryError:
        record = None
    if record is None:
        update_workspace_details(
            dataset_id,
            name=workspace.record.name,
            description=workspace.record.description,
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
            fallback_record=workspace.record,
        )


def _require_report_run(dataset_id: str, report_id: str) -> None:
    try:
        reports = list_generated_report_versions(
            dataset_id,
            generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
        )
    except ReportNarrationError as error:
        abort(422, description=str(error))
    if not any(report.report_id == report_id for report in reports):
        abort(404)


def _ensure_report_active(
    dataset_id: str,
    report_id: str,
    *,
    mutable: bool = False,
) -> None:
    try:
        record = load_workspace_record(
            dataset_id,
            workspace_dir=Path(current_app.config["WORKSPACE_DIR"]),
        )
    except WorkspaceHistoryError as error:
        abort(422, description=str(error))
    if record is not None and report_id in record.archived_report_ids:
        abort(404, description="This report is in recoverable trash.")
    if mutable and record is not None and record.is_archived:
        abort(
            409,
            description=("Restore the workspace before changing its reports."),
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
                column.name for column in profile.columns if column.inferred_type.value == "numeric"
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
                    not in {metric.name.casefold() for metric in configuration.metrics}
                )
                if configuration is not None
                else profile.kpi_candidates
            ),
            source_aggregation_options=SOURCE_AGGREGATIONS,
            display_format_options=DISPLAY_FORMATS,
            target_scope_options=TARGET_SCOPES,
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
                column.name for column in profile.columns if column.inferred_type.value == "numeric"
            ),
            aggregation_options=("sum", "mean", "median", "min", "max", "formula"),
            display_format_options=("number", "percentage", "currency"),
            configuration_error=configuration_error,
            form_data=form_data,
            dataset_context=dataset_context,
            configuration=configuration,
            target_scope_options=TARGET_SCOPES,
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
            source_aggregation_options=SOURCE_AGGREGATIONS,
            display_format_options=DISPLAY_FORMATS,
            target_scope_options=TARGET_SCOPES,
            evidence_ready=(
                Path(current_app.config["EVIDENCE_DIR"]) / f"{configuration.dataset_id}.json"
            ).is_file(),
            report_configuration_ready=_report_configuration_path(
                configuration.dataset_id
            ).is_file(),
            configuration_json=json.dumps(configuration.to_dict(), indent=2, sort_keys=True),
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
        "replaces_visualization_id": values.get("replaces_visualization_id", ""),
    }


def _default_visualization_form(
    profile: DatasetProfile,
    *,
    configuration: BusinessConfiguration | None,
) -> MultiDict[str, str]:
    """Build a valid, useful starting point for the guided chart builder."""

    values = MultiDict[str, str]()
    values["purpose"] = ""
    values["aggregation"] = "configured"
    values["date_granularity"] = "month"
    values["filter_mode"] = "include"
    values["sort_by"] = "value"
    values["sort_direction"] = "descending"
    values["top_n"] = "10"
    values["scale"] = "linear"
    values["bin_count"] = "10"
    values["include_in_report"] = "yes"

    measure_label = "Record count"
    measure_selector = "count:records"
    if configuration is not None:
        primary = configuration.primary_metric
        measure_label = primary.name
        measure_selector = f"metric:{primary.metric_id}"
    else:
        numeric_column = next(
            (
                column.name
                for column in profile.columns
                if column.inferred_type.value == "numeric"
                and not column.is_constant
                and not column.is_empty
            ),
            None,
        )
        if numeric_column is not None:
            measure_label = numeric_column
            measure_selector = f"column:{numeric_column}"
    values.setlist("measure_selectors", [measure_selector])

    if profile.category_candidates:
        category = profile.category_candidates[0]
        values["chart_type"] = "category_bar"
        values["title"] = f"{measure_label} by {category}"
        values["x_column"] = category
    elif profile.date_candidates:
        date_column = profile.date_candidates[0]
        values["chart_type"] = "time_line"
        values["title"] = f"{measure_label} over time"
        values["x_column"] = date_column
    elif measure_selector.startswith(("metric:", "column:")):
        values["chart_type"] = "histogram"
        values["title"] = f"{measure_label} distribution"
    else:
        values["chart_type"] = "category_bar"
        values["title"] = "Record count"
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
    if configuration is not None:
        evidence_path = Path(current_app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json"
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
    tuple[ManualVisualizationArtifact, ...],
]:
    configuration = _require_configuration(dataset_id, profile)
    evidence_path = Path(current_app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json"
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
    manual_boards = list_manual_visualizations(
        dataset_id=dataset_id,
        source_sha256=profile.source_sha256,
        visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
    )
    return configuration, evidence, visualizations, manual_boards


def _current_report_package(
    dataset_id: str,
    profile: DatasetProfile,
) -> tuple[
    BusinessConfiguration,
    dict[str, object] | None,
    tuple[VisualizationArtifact, ...],
    tuple[ManualVisualizationArtifact, ...],
    ReportGenerationPackage,
]:
    configuration, evidence, visualizations, manual_boards = _report_assets(
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
        manual_boards=manual_boards,
    )
    package = build_report_generation_package(
        report,
        configuration=configuration,
        evidence_payload=evidence,
        visualizations=visualizations,
        visualization_insights=_load_visualization_insights(
            (*visualizations, *manual_boards)
        ),
        manual_boards=manual_boards,
    )
    return configuration, evidence, visualizations, manual_boards, package


def _load_visualization_insights(
    visualizations: tuple[
        VisualizationArtifact | ManualVisualizationArtifact,
        ...,
    ],
) -> tuple[VisualizationInsightArtifact, ...]:
    """Load only insight artifacts that still match their saved charts."""

    insight_dir = Path(current_app.config["VISUALIZATION_INSIGHT_DIR"])
    loaded = [
        insight
        for artifact in visualizations
        if (
            insight := load_visualization_insight(
                artifact,
                insight_dir=insight_dir,
            )
        )
        is not None
    ]
    return tuple(loaded)


def _current_package_sha256(dataset_id: str) -> str | None:
    """Return the current report-package fingerprint when it is rebuildable."""

    try:
        profile = _load_profile(dataset_id)
        (
            _configuration,
            _evidence,
            _visualizations,
            _manual_boards,
            package,
        ) = _current_report_package(dataset_id, profile)
    except (
        BusinessConfigurationError,
        DatasetProfileError,
        DatasetValidationError,
        DatasetViewError,
        EvidenceError,
        HTTPException,
        ReportConfigurationError,
        ReportGenerationPackageError,
        VisualizationError,
    ):
        return None
    return artifact_sha256(package.to_dict())


def _render_generated_report(
    report: GeneratedReport,
    *,
    historical_snapshot: bool,
    report_pdf_url: str,
    report_json_url: str,
):  # type: ignore[no-untyped-def]
    published_stories = included_report_stories(report)
    published_summary_points = included_executive_summary_points(report)
    chart_url_by_id: dict[str, str] = {}
    historical_chart_ids = (
        set(_historical_report_chart_paths(report)) if historical_snapshot else set()
    )
    for item in report.items:
        if historical_snapshot:
            if item.evidence_id in historical_chart_ids:
                chart_url_by_id[item.evidence_id] = url_for(
                    "core.generated_report_version_chart",
                    dataset_id=report.dataset_id,
                    report_id=report.report_id,
                    version=report.version,
                    evidence_id=item.evidence_id,
                )
        elif item.chart_filename is not None:
            chart_url_by_id[item.evidence_id] = url_for(
                "core.evidence_chart",
                dataset_id=report.dataset_id,
                evidence_id=item.evidence_id,
            )
        elif item.visualization_id is not None:
            endpoint = (
                "core.saved_manual_visualization_chart"
                if item.visualization_id.startswith("MBV-")
                else "core.saved_visualization_chart"
            )
            chart_url_by_id[item.evidence_id] = url_for(
                endpoint,
                dataset_id=report.dataset_id,
                visualization_id=item.visualization_id,
            )
    return render_template(
        "generated_report.html",
        report=report,
        historical_snapshot=historical_snapshot,
        report_pdf_url=report_pdf_url,
        report_json_url=report_json_url,
        chart_url_by_id=chart_url_by_id,
        published_stories=published_stories,
        published_summary_points=published_summary_points,
        story_sections=_generated_story_sections(published_stories),
        sections=_generated_report_sections(report.items),
        item_by_id={item.evidence_id: item for item in report.items},
        report_json=json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
    )


def _generated_report_chart_paths(
    report: GeneratedReport,
    *,
    visualizations: tuple[VisualizationArtifact, ...],
    manual_boards: tuple[ManualVisualizationArtifact, ...] = (),
) -> dict[str, Path]:
    chart_dir = Path(current_app.config["CHART_DIR"])
    visualization_by_id = {
        artifact.visualization_id: artifact
        for artifact in visualizations
        if artifact.visualization_id is not None
    }
    chart_paths: dict[str, Path] = {}
    manual_board_by_id = {
        artifact.visualization_id: artifact for artifact in manual_boards
    }
    for item in report.items:
        filename = item.chart_filename
        if filename is None and item.visualization_id is not None:
            artifact = visualization_by_id.get(item.visualization_id)
            if artifact is not None:
                filename = artifact_chart_filename(artifact)
            else:
                manual_board = manual_board_by_id.get(item.visualization_id)
                if manual_board is not None:
                    png_path = manual_visualization_png_path(
                        manual_board,
                        visualization_dir=Path(current_app.config["VISUALIZATION_DIR"]),
                    )
                    if png_path is not None:
                        chart_paths[item.evidence_id] = png_path
                    continue
        if filename is None:
            continue
        chart_path = chart_dir / filename
        if chart_path.is_file():
            chart_paths[item.evidence_id] = chart_path
    return chart_paths


def _save_generated_report_with_charts(
    report: GeneratedReport,
    *,
    visualizations: tuple[VisualizationArtifact, ...],
    manual_boards: tuple[ManualVisualizationArtifact, ...] = (),
) -> tuple[GeneratedReport, Path]:
    """Save one report and roll it back if its chart snapshot cannot persist."""

    saved, report_path = save_generated_report(
        report,
        generated_report_dir=Path(current_app.config["GENERATED_REPORT_DIR"]),
    )
    try:
        snapshot_generated_report_charts(
            saved,
            _generated_report_chart_paths(
                saved,
                visualizations=visualizations,
                manual_boards=manual_boards,
            ),
            generated_report_asset_dir=Path(current_app.config["GENERATED_REPORT_ASSET_DIR"]),
        )
    except ReportNarrationError:
        report_path.unlink(missing_ok=True)
        raise
    return saved, report_path


def _historical_report_chart_paths(
    report: GeneratedReport,
) -> dict[str, Path]:
    """Resolve only chart filenames retained inside an immutable report."""

    snapshots = generated_report_chart_snapshots(
        report,
        generated_report_asset_dir=Path(current_app.config["GENERATED_REPORT_ASSET_DIR"]),
    )
    if snapshots:
        return snapshots
    chart_dir = Path(current_app.config["CHART_DIR"])
    return {
        item.evidence_id: chart_dir / item.chart_filename
        for item in report.items
        if item.chart_filename is not None and (chart_dir / item.chart_filename).is_file()
    }


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
            tuple(story for story in stories if story.section == section),
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
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise EvidenceError("Saved evidence records are invalid.")

    def sort_key(record: dict[str, object]) -> tuple[int, str]:
        ranking = record.get("ranking")
        rank = ranking.get("rank") if isinstance(ranking, dict) else None
        return (
            rank if isinstance(rank, int) and rank > 0 else 1_000_000,
            str(record.get("id", "")),
        )

    return tuple(sorted(records, key=sort_key))


def _evidence_kind(record: dict[str, object]) -> str:
    insight_type = record.get("insight_type")
    if insight_type in _DIAGNOSTIC_EVIDENCE_TYPES:
        return "diagnostic"
    if insight_type in _ASSOCIATION_EVIDENCE_TYPES:
        return "association"
    return "finding"


def _recommended_evidence_ids(
    configuration: BusinessConfiguration,
    evidence_payload: dict[str, object] | None,
) -> tuple[str, ...]:
    """Choose a bounded management-first default while preserving all choices."""

    eligible = [
        record
        for record in _sorted_evidence_records(evidence_payload)
        if _evidence_kind(record) != "diagnostic" and isinstance(record.get("id"), str)
    ]
    selected: list[dict[str, object]] = []
    selected_ids: set[str] = set()
    correlation_count = 0

    def add(record: dict[str, object]) -> None:
        nonlocal correlation_count
        record_id = str(record["id"])
        if record_id in selected_ids or len(selected) >= _MAX_DEFAULT_EVIDENCE:
            return
        if _evidence_kind(record) == "association":
            if correlation_count >= _MAX_DEFAULT_ASSOCIATIONS:
                return
            correlation_count += 1
        selected.append(record)
        selected_ids.add(record_id)

    # Give every configured KPI one factual anchor before filling remaining
    # slots by management relevance. metric_snapshot normally supplies it.
    for metric in configuration.metrics:
        candidate = next(
            (
                record
                for record in eligible
                if record.get("metric_id") == metric.metric_id
                and _evidence_kind(record) == "finding"
            ),
            None,
        )
        if candidate is not None:
            add(candidate)
    for record in eligible:
        add(record)
    return tuple(str(record["id"]) for record in selected)


def _default_report_form(
    configuration: BusinessConfiguration,
    *,
    evidence_payload: dict[str, object] | None,
    visualizations: tuple[VisualizationArtifact, ...],
    manual_boards: tuple[ManualVisualizationArtifact, ...] = (),
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
        list(
            _recommended_evidence_ids(
                configuration,
                evidence_payload,
            )
        ),
    )
    values.setlist(
        "selected_visualization_ids",
        [
            artifact.visualization_id
            for artifact in visualizations
            if artifact.visualization_id is not None and artifact.spec.include_in_report
        ],
    )
    values.setlist(
        "selected_manual_board_ids",
        [
            artifact.visualization_id
            for artifact in manual_boards
            if artifact.png_filename is not None
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
    values["include_evidence_appendix"] = "yes" if report.include_evidence_appendix else ""
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
    values.setlist(
        "selected_manual_board_ids",
        list(report.selected_manual_board_ids),
    )
    return values


def _report_configuration_path(dataset_id: str) -> Path:
    return Path(current_app.config["REPORT_CONFIGURATION_DIR"]) / f"{dataset_id}.json"


def _render_visualization(
    artifact: VisualizationArtifact,
    *,
    artifact_json: str,
    preview_token: str | None = None,
    configuration: BusinessConfiguration | None = None,
    visualization_insight: VisualizationInsightArtifact | None = None,
    insight_notice: str | None = None,
    insight_error: str | None = None,
):  # type: ignore[no-untyped-def]
    return render_template(
        "visualization.html",
        artifact=artifact,
        artifact_json=artifact_json,
        preview_token=preview_token,
        configuration=configuration,
        visualization_insight=visualization_insight,
        insight_notice=insight_notice,
        insight_error=insight_error,
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


def _require_configuration(dataset_id: str, profile: DatasetProfile) -> BusinessConfiguration:
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


def _populate_formula_fields(form_data: MultiDict[str, str], metric: DerivedMetric) -> None:
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
    **additional_route_values: str,
):  # type: ignore[no-untyped-def]
    token = save_navigation_state(payload, state_dir=_navigation_state_dir())
    route_values: dict[str, str] = {"state": token}
    if dataset_id is not None:
        route_values["dataset_id"] = dataset_id
    route_values.update(additional_route_values)
    return redirect(url_for(endpoint, **route_values), code=303)


def _redirect_profile_state(dataset_id: str, *, status_code: int = 200, **values: object):  # type: ignore[no-untyped-def]
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


def _load_view_state(expected_view: str, *, dataset_id: str | None = None) -> dict[str, object]:
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
        if (
            not isinstance(key, str)
            or not isinstance(items, list)
            or not all(isinstance(item, str) for item in items)
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


# Operational endpoint


@core.get("/health")
def health():  # type: ignore[no-untyped-def]
    """Return a non-sensitive process health signal."""

    return jsonify(service="ai-insight-reporter", status="ok")
