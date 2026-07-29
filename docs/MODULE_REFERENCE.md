# Module and Function Reference

This reference explains the Python package at
`src/insight_reporter/`. It is intended for maintainers who need to locate a
change quickly without reading every file first.

Names without a leading underscore are module interfaces used by routes, other
domain modules, or tests. Names beginning with `_` are implementation details;
the important groups are described so their purpose is still discoverable.

## Package entry points

### `__init__.py`

Exports `create_app`, allowing Flask to start the application with:

```bash
python -m flask --app insight_reporter:create_app run
```

### `app.py`

Creates the Flask process and runtime directory layout.

- `create_app(test_config=None)` loads `DefaultConfig`, applies test
  overrides, creates artifact directories, configures redacted logging,
  registers the `core` blueprint, and attaches security headers.
- `_ARTIFACT_DIRECTORIES` is the single mapping between Flask configuration
  keys and subdirectories under `instance/`, including persistent workspace
  metadata.

Primary tests: `tests/test_app.py`, `tests/test_config.py`,
`tests/test_logging_config.py`.

### `config.py`

Defines environment-backed defaults.

- `DefaultConfig` contains upload limits, preview limits, artifact paths,
  Ollama model/host/timeout/temperature, trusted hosts, and Flask security
  defaults.
- `_positive_int`, `_bounded_float`, `_log_level`, and `_ollama_model` parse
  environment variables and fall back to safe values.

Environment names and defaults are listed in `.env.example` and the README.

### `logging_config.py`

Prevents sensitive values from being written directly to logs.

- `redact_sensitive_text(value)` removes risky control characters and bounds
  free text.
- `SensitiveDataFilter` sanitizes log records before emission.
- `configure_logging(app)` installs the filter and configured log level.

### `workspace_history.py`

Owns Milestone 6A workspace identity and reconstructs progress from filesystem
artifacts without introducing a database.

Core contracts:

- `WorkspaceDirectories` names every artifact directory required to inspect
  workflow progress.
- `WorkspaceRecord` is the versioned durable identity created for a new
  upload. It stores the safe display name, original filename, internal
  filename, source format, fingerprint, size, and creation time.
- `WorkspaceSummary` adds derived stage, last activity, immutable report
  version count, generation-run count, and any legacy-metadata warning.

Public functions:

- `create_workspace_record(upload, original_filename, workspace_dir)` creates
  metadata only after the source passed ingestion.
- `rename_workspace(...)` changes the presentation name without changing the
  dataset ID or any analytical artifact.
- `load_workspace_record(...)` parses and validates one exact metadata file.
- `get_workspace_summary(...)` combines one retained source with its current
  downstream artifacts.
- `list_workspace_summaries(...)` discovers all retained datasets and orders
  them by latest activity.

Important internals:

- `_parse_workspace_record` treats persisted JSON as untrusted when reopening
  it.
- `_legacy_workspace_record` gives pre-6A uploads a safe fallback identity;
  renaming that workspace materializes a normal versioned record.
- `_workspace_stage` derives uploaded, configured, analyzed, report-ready, or
  generated status.
- `_report_counts` distinguishes immutable versions from independent report
  IDs.
- `_clean_original_filename` removes browser-supplied path components and
  control characters.

Primary test: `tests/test_workspace_history.py`.

## Dataset ingestion and access

### `dataset_ingestion.py`

Owns the one-file upload lifecycle.

- `DatasetValidationError` carries a safe message and HTTP status.
- `DatasetUploadResult` records the randomized filename, detected format,
  hash, size, shape, and available XLSX worksheet names.
- `ingest_dataset(...)` streams the upload into a temporary file, enforces the
  byte limit, detects its real format, validates it through `DatasetView`, and
  atomically promotes only a valid source.
- `find_dataset_path(upload_dir, dataset_id)` safely resolves a server-created
  dataset ID to one retained CSV, JSON, or XLSX file.
- `save_xlsx_selection(...)` and `load_xlsx_selection(...)` persist the
  explicitly selected visible worksheet.
- `_selection_path(...)` creates the server-controlled worksheet-selection
  path.

Primary tests: `tests/test_csv_upload.py`,
`tests/test_multiformat_ingestion.py`.

### `dataset_view.py`

Provides the format-neutral table boundary used by downstream code.

Core data contracts:

- `SourceManifest` identifies the retained file, selected worksheet, hash,
  format, row count, and column count.
- `ColumnReference` identifies a column inside one source.
- `DatasetRow` stores the original row number and normalized values.
- `DatasetView` defines `headers`, `sources`, and `iter_rows()`.
- `CsvDatasetView`, `JsonDatasetView`, and `XlsxDatasetView` implement that
  contract for each supported format.

Public functions:

- `load_dataset_view(path, ...)` selects the correct implementation and
  applies row/column limits.
- `detect_dataset_format(raw_bytes)` identifies content rather than trusting
  the extension.
- `discover_xlsx_tables(path)` returns safe visible worksheet names.
- `source_id_from_hash(...)` creates a stable source identity.
- `load_source_manifest(...)` and `load_column_reference(...)` validate saved
  source-aware objects.

Important internal groups:

- `_validate_headers`, `_normalized_header`, `_validate_safe_text`,
  `_validate_row_limit`, and `_require_rows` enforce the common tabular
  contract.
- `_unique_json_object`, `_reject_json_constant`, and `_json_scalar` keep JSON
  flat, finite, and duplicate-key free.
- `_validate_xlsx_archive`, `_load_xlsx_workbook`, `_visible_sheet_names`, and
  `_xlsx_cell_value` enforce the XLSX security boundary.

Primary test: `tests/test_dataset_view.py`.

### `dataset_profile.py`

Turns a `DatasetView` into deterministic metadata.

Core contracts:

- `ColumnType` enumerates numeric, categorical, datetime, boolean, identifier,
  free-text, and empty classifications.
- `NumericStatistics`, `DateRange`, `ColumnProfile`, and `DatasetProfile`
  represent calculated profile data.

Public functions:

- `profile_dataset(view, ...)` profiles a normalized dataset.
- `profile_csv(path, ...)` is a compatibility wrapper for CSV callers.

Important internals:

- `_profile_column` calculates missingness, uniqueness, sample values, numeric
  statistics, and date ranges.
- `_infer_type` combines parsing results and name/value heuristics.
- `_identifier_name`, `_value_pattern_looks_like_identifier`, and
  `_looks_like_free_text` prevent IDs and narrative text from becoming KPIs.
- `_is_missing`, `_is_number`, and `_parse_datetime` implement shared profile
  parsing semantics.

Primary test: `tests/test_dataset_profile.py`.

## Configuration and formulas

### `business_config.py`

Owns the reviewed KPI registry for one dataset.

Core contracts:

- `MetricConfiguration` stores one source or derived KPI and its direction,
  benchmark, role, aggregation, dimensions, and formula definition.
- `BusinessConfiguration` stores source identity, shared date/categories and
  objective, one-to-five metrics, and the primary metric ID.

Public functions:

- `validate_business_configuration(...)` validates the first or additional
  source KPI configuration.
- `validate_derived_business_configuration(...)` validates a derived KPI
  before registration.
- `add_source_metrics(...)` appends reviewed source KPIs without replacing
  existing metrics.
- `set_primary_metric(...)`, `remove_metric(...)`, and
  `update_metric_settings(...)` perform bounded registry edits.
- `save_business_configuration(...)` writes atomically under
  `instance/configurations/`.
- `load_business_configuration(...)` validates current and supported legacy
  schemas.

Important internals:

- `_build_configuration`, `_source_metric`, and `_derived_registry_metric`
  construct canonical objects.
- `_metric_id` creates stable metric identities.
- `_deduplicate_metrics` prevents overlapping KPI definitions.
- `_load_registry_configuration`, `_load_metric`, and
  `_load_legacy_configuration` implement backward-compatible loading.

Primary tests: `tests/test_business_config.py`,
`tests/test_business_config_route.py`.

### `formula_engine.py`

Implements the restricted derived-KPI language; it never evaluates arbitrary
Python.

Core contracts:

- `ParsedFormula` stores the validated expression tree, referenced columns,
  scope, and display label.
- `FormulaEvaluation` records a calculated value or an explicit missing/error
  reason.
- `_Parser` is the recursive-descent parser for approved tokens and functions.

Public functions:

- `parse_formula(text, allowed_columns)` tokenizes, parses, and validates a
  formula.
- `load_parsed_formula(payload)` reconstructs a saved parsed formula.
- `evaluate_row_formula(...)` calculates one row-scoped result.
- `evaluate_aggregate_formula(...)` calculates an aggregate-scoped result.
- `aggregate_row_values(...)` aggregates already evaluated row results.

Important internals:

- `_tokenize` accepts bracketed column references, finite numbers, approved
  operators, parentheses, and approved aggregate functions.
- `_evaluate` recursively calculates the parsed expression.
- `_references`, `_scope_counts`, and `_expression_stats` enforce consistent
  row versus aggregate scope.
- `_finite_result` and `_finite_number` reject NaN and infinity.

Primary test: `tests/test_formula_engine.py`.

### `derived_metrics.py`

Connects formula definitions to dataset values and business configuration.

Core contracts:

- `DerivedMetric` is the saved formula definition and presentation metadata.
- `DerivedEvaluation` stores row-level results.
- `DerivedMetricPreview` summarizes valid, missing, zero-division, and
  non-finite outcomes.

Public functions:

- `validate_derived_metric(...)` validates legacy operation-based definitions.
- `validate_formula_metric(...)` validates current formula-based definitions.
- `load_derived_metric(...)` loads supported schemas.
- `evaluate_derived_metric(...)` calculates row-level values.
- `aggregate_derived_metric(...)` calculates the KPI at dataset, period, or
  segment scope.
- `preview_derived_metric(...)` returns a user-reviewable calculation preview.
- `convert_legacy_metric_to_formula(...)` migrates supported old definitions.

Primary tests: `tests/test_derived_metrics.py`,
`tests/test_derived_kpi_routes.py`.

## Optional AI configuration assistance

### `configuration_suggestions.py`

Requests source-KPI configuration suggestions from local Ollama.

- `ConfigurationSuggestion` and `SuggestionBatch` are validated outputs.
- `build_suggestion_response_schema(...)` restricts returned KPI, date, and
  category names to supplied profile candidates.
- `build_profile_summary(profile, ...)` creates compact metadata without raw
  rows.
- `generate_configuration_suggestions(...)` calls Ollama.
- `parse_suggestion_response(...)` parses JSON and revalidates every field.
- `_validate_suggestion`, `_bounded_string`, and `_response_content` enforce
  the model-output trust boundary.

Primary tests: `tests/test_configuration_suggestions.py`,
`tests/test_suggestion_routes.py`.

### `derived_kpi_suggestions.py`

Requests optional formula ideas from local Ollama.

- `DerivedKpiSuggestion` and `DerivedKpiSuggestionBatch` are advisory output
  contracts.
- `build_derived_kpi_response_schema(...)` limits source columns, operations,
  aggregations, and output shape.
- `build_derived_kpi_profile_summary(...)` sends bounded numeric metadata.
- `generate_derived_kpi_suggestions(...)` performs the local model call.
- `parse_derived_kpi_response(...)` and `_validate_suggestion(...)` reject
  unknown columns, invalid definitions, duplicate names, and unsafe output.
- `_numeric_candidate_columns` removes constants and identifier-like columns.

Primary tests: `tests/test_derived_kpi_suggestions.py`,
`tests/test_derived_kpi_routes.py`.

## Deterministic analysis and evidence

### `insight_engine.py`

Calculates facts for every configured KPI.

Core contracts:

- `Insight` contains one observation, metric ID, source columns, filters,
  record support, confidence, and limitations.
- `InsightReport` binds all insights to the dataset, source fingerprints, and
  KPI definitions.
- `_Collector` assigns stable `INS-...` IDs while algorithms run.

Public functions:

- `generate_insights(source, profile, configuration)` validates all source
  identities and executes applicable algorithms for each KPI.
- `save_insight_report(...)` writes deterministic JSON atomically.

Analysis functions:

- `_add_missing_data_insights` creates dataset-quality warnings.
- `_prepare_temporal_context` validates dates and groups rows by period.
- `_add_period_change` compares adjacent valid periods.
- `_add_trend` calculates a linear direction over valid periods.
- `_add_segment_ranking` compares category aggregates.
- `_add_segment_benchmark_performance` identifies the best and worst
  target-attainment segments and calculates per-segment average values, gaps,
  breach counts, and breach percentages.
- `_add_segment_contribution` reconciles segment changes to the total change.
- `_add_anomalies` applies Tukey’s IQR rule.
- `_add_correlations` calculates Pearson associations.
- `_add_benchmark_breaches` counts values crossing the configured threshold.

Calculation helpers such as `_metric_value`, `_aggregate_metric`, `_pearson`,
`_linear_trend`, `_direction`, `_favorable`, and `_count_confidence` keep the
algorithms deterministic and testable.

Primary test: `tests/test_insight_engine.py`.

### `evidence_layer.py`

Transforms deterministic insights into traceable report evidence.

Core contracts:

- `EvidenceRanking` contains impact, confidence, relevance, combined score,
  and final rank.
- `ChartArtifact` identifies an automatic chart.
- `EvidenceRecord` combines an insight with source traceability, calculation
  details, supporting data, ranking, limitations, and optional chart.
- `EvidenceReport` stores the versioned evidence set.

Public functions:

- `generate_evidence(...)` creates one evidence record per insight.
- `save_evidence_report(...)` saves evidence atomically.
- `load_evidence_payload(...)` loads saved evidence for report selection.
- `chart_filename_for(...)` resolves a validated evidence chart.
- `referenced_chart_filenames(...)` finds charts still used by evidence.
- `delete_chart_files(...)` removes superseded automatic charts.

Important internals:

- `_supporting_data`, `_observation_row`, `_correlation_pairs`, and `_periods`
  build reviewer-readable context.
- `_calculation_description` explains how each insight was produced.
- `_evidence_id` creates stable evidence identities.
- `_ranking`, `_impact_score`, and `_assign_ranks` prioritize evidence;
  material target gaps, period changes, and segment findings receive greater
  management relevance than associations and technical warnings.
- `_chart_type_for` and `_generate_chart` choose and render deterministic
  chart types, including segment target-performance bars.

Primary test: `tests/test_evidence_layer.py`.

## Manual visualizations

### `visualization_builder.py`

Owns manual chart validation, calculation, rendering, preview, and persistence.

Core contracts:

- `VisualizationSpec` is the validated user request.
- `VisualizationMeasure` identifies KPI-backed or supplementary measures.
- `ManualChart` contains chart metadata and supporting data.
- `VisualizationArtifact` is the saved, source-bound chart definition.

Public functions:

- `parse_visualization_spec(...)` validates submitted form values.
- `build_visualization(...)` resolves measures, filters rows, calculates
  grouped values, and renders the chart.
- `save_preview(...)`, `load_preview(...)`, and `delete_preview(...)` manage
  short-lived preview artifacts.
- `save_visualization(...)`, `load_visualization(...)`, and
  `list_visualizations(...)` manage saved charts.
- `delete_chart(...)` removes a superseded chart image.
- `artifact_chart_filename(...)` resolves a saved chart safely.
- `spec_to_form(...)` repopulates the editor when reopening a chart.

Important internals:

- `_resolve_measure` and `_validate_compatibility` enforce chart/measure rules.
- `_filter_rows`, `_grouped_data`, `_row_measure_value`, and
  `_group_measure_value` calculate displayed values.
- `_render_chart`, `_render_line`, `_render_bars`, `_render_scatter`, and
  `_render_box` produce images through Matplotlib’s non-interactive backend.
- `_validate_dataset` and `_source_metadata` bind artifacts to the source.

Primary test: `tests/test_visualization_builder.py`.

### `manual_visualization_evidence.py`

Creates deterministic evidence from a saved manual chart.

- `ManualVisualizationEvidence` stores chart purpose, classification, source,
  measures, filters, observations, supporting data, and limitations.
- `generate_manual_visualization_evidence(artifact)` derives evidence from the
  actual plotted values.
- `_aggregate_observations` calculates displayed extrema.
- `_time_changes` calculates changes between displayed periods.
- `_scatter_observation` calculates a displayed association.
- `_distribution_observation` and `_quartile_halves` calculate descriptive
  distribution facts.
- `_manual_evidence_id` creates a stable `MVE-...` identity.

Primary test: `tests/test_manual_visualization_evidence.py`.

### `dataset_context.py`

Builds the safe context panels used beside formula and report fields.

- `ContextItem` represents a selectable source-aware token.
- `DatasetContext` groups KPI, numeric, categorical, boolean, date,
  visualization, and evidence items.
- `build_dataset_context(...)` builds the browser-facing context.
- `build_model_context(...)` builds bounded model-facing metadata.
- `context_token_maps(...)` maps stable tokens back to approved items.
- `formula_insert_text(...)` creates bracketed formula references.
- `_saved_visualization_context` and `_evidence_context` add downstream
  artifacts without exposing raw rows.

## Report selection and package construction

### `report_configuration.py`

Validates exactly what will appear in or support a report.

- `ReportConfiguration` stores presentation settings, selected metric IDs,
  evidence IDs, visualization IDs, source metadata, and dependency
  fingerprints.
- `validate_report_configuration(...)` checks required text, choices,
  selection uniqueness, KPI/evidence ownership, visualization dependencies,
  and source consistency.
- `save_report_configuration(...)` and `load_report_configuration(...)`
  persist and revalidate the selection.
- `artifact_sha256(payload)` creates canonical JSON fingerprints used
  throughout report staleness checks.
- `_evidence_records`, `_evidence_sort_key`, and
  `_validate_visualization_source` enforce selection integrity.

Primary tests: `tests/test_report_configuration.py`,
`tests/test_report_configuration_routes.py`.

### `report_generation_package.py`

Builds the exact bounded contract consumed by report narration.

- `ReportGenerationPackage` contains report settings, sources, selected KPI
  definitions, compact deterministic evidence, manual-visualization evidence,
  omissions, and the model-input policy.
- `build_report_generation_package(...)` verifies fingerprints and selected
  dependencies, then removes raw rows and row identities.
- `save_report_generation_package(...)` saves the inspectable package under
  `instance/report_packages/`.
- `_compact_evidence` and `_strip_row_identity` bound supporting data and
  remove row-level identity.

## Narration, summaries, versioning, and PDF

### `report_narration.py`

Owns the model-output trust boundary and generated-report JSON lifecycle.

Core contracts:

- `NarratedFactReference` points to one exact Python fact and formatted value.
- `NarrativeStory` contains the validated story fields and story provenance.
  Its Python-derived `business_context` records path-labelled product, region,
  segment, category, cohort, or period values already present in deterministic
  evidence.
- `ExecutiveSummaryPoint` separates one management finding into `text`,
  `business_implication`, and `recommended_action`, with supporting story IDs,
  exact facts, and narration source.
- `NarratedEvidence` is the report-facing copy of one evidence record.
- `GeneratedReport` is the immutable, versioned report artifact.

Public functions:

- `generate_narrated_report(...)` converts package evidence into story packs,
  generates validated stories, generates the five-point executive summary,
  records fallbacks, and returns an unsaved report.
- `publish_report_presentation(...)` validates story inclusion and order
  without another model call.
- `regenerate_generated_story(...)` regenerates one stable story while
  preserving the others.
- `included_report_stories(...)` returns published stories in display order.
- `included_executive_summary_points(...)` returns summary points still
  supported by included stories.
- `save_generated_report(...)` appends an immutable version.
- `load_generated_report(...)` and `latest_generated_report(...)` perform
  path-safe loading, schema validation, provenance validation, and optional
  package-fingerprint checks.
- `load_generated_report_version(...)` loads one exact immutable filename
  instead of silently advancing to the newest revision for its report ID.
- `list_generated_report_versions(...)` validates and returns complete
  dataset history in newest-first order.
- `snapshot_generated_report_charts(...)` atomically copies a version's
  automatic and manual charts into its immutable history directory.
- `generated_report_chart_snapshots(...)` resolves only assets belonging to
  one exact dataset/report/version identity.

Generation internals:

- `_story_packs` groups bounded evidence by metric.
- `_generate_story`, `_story_response_schema`, and `_parse_story_response`
  implement structured generation and validation-aware retries.
- `_generate_executive_summary`, `_summary_response_schema`, and
  `_parse_summary_response` do the same for exactly five prioritized,
  actionable management points. Each point must name its metric, quote an
  exact selected fact, and include a concrete review, comparison, validation,
  investigation, or monitoring action.
- `_story_context_descriptors` supplies path-labelled context so a model can
  distinguish the current quarter from the previous quarter or the worst
  region from another segment without inventing either.
- `_story_business_context_descriptors` selects the bounded subset useful to
  business readers. `_require_business_context` rejects a management finding
  that omits every available verified name.
- `_resolve_story_fact_references` and
  `_resolve_summary_fact_references` independently map model-selected
  references back to Python values.
- `_validate_commentary` rejects invented numbers, unsupported units,
  quantitative number words, and causal wording.
- `_deterministic_story` and `_deterministic_executive_summary` provide
  explicitly labelled safe fallbacks.
- `_parse_generation_diagnostics` revalidates accepted-story counts, fallback
  counts, rejected story IDs, summary provenance, and the grounding policy
  whenever a saved report is reopened.
- `_parse_report`, `_parse_story`, `_parse_item`, and the `_parse_saved_*`
  functions revalidate every field when reopening JSON.

Primary test: `tests/test_report_narration.py`.

### `report_pdf.py`

Renders a validated `GeneratedReport`; it never asks Ollama or recalculates
insights.

- `report_pdf_filename(report)` creates a safe, versioned download name.
- `build_report_pdf(report, chart_paths)` renders the same included summary,
  stories, claims, charts, sources, limitations, and optional appendix as the
  HTML report.
- `_story_flowables` builds one story section.
- `_chart_image` bounds chart dimensions.
- `_kpi_table` and `_metadata_table` render structured metadata.
- `_styles`, `_register_fonts`, and `_page_frame` define print presentation.
- `_markup` and `_plain_text` escape untrusted content.

Primary test: `tests/test_report_pdf.py`.

## HTTP orchestration and navigation

### `navigation_state.py`

Supports POST/Redirect/GET without putting form data or errors in URLs.

- `save_navigation_state(...)` stores a small, expiring JSON payload.
- `load_navigation_state(...)` consumes and deletes one state token.
- `_delete_expired_states(...)` removes state older than the retention window.

Primary tests: `tests/test_navigation_state.py`,
`tests/test_navigation_routes.py`.

### `routes.py`

Contains the Flask `core` blueprint. Routes should orchestrate domain modules;
new calculations should not be added here.

Dataset and KPI routes:

- `upload_form`, `upload_dataset`
- `dataset_profile`, `excel_sheet_selection`, `select_excel_sheet`
- `suggest_configurations`, `review_suggestion`
- `suggest_derived_kpis`, `review_derived_kpi`, `derived_kpi_editor`
- `configure_dataset`, `configure_derived_kpi`
- `saved_configuration`, `choose_primary_metric`,
  `edit_metric_settings`, `remove_configured_metric`

Workspace routes:

- `workspace_history`, `workspace_detail`, `update_workspace_name`

Report routes:

- `report_configuration_form`, `configure_report`,
  `saved_report_configuration`
- `report_generation_package`, `generate_report`, `latest_report`
- `generated_report`, `generated_report_json`, `generated_report_pdf`
- `publish_generated_report`, `regenerate_report_story`
- `generated_report_history`, `generated_report_version`,
  `generated_report_version_json`, `generated_report_version_pdf`,
  `generated_report_version_chart`

Visualization routes:

- `saved_visualizations`, `visualization_builder`,
  `preview_visualization`, `visualization_preview`,
  `visualization_preview_chart`, `confirm_visualization`
- `saved_visualization`, `saved_visualization_chart`,
  `regenerate_visualization`

Insight and evidence routes:

- `deterministic_insights`, `saved_insights`, `evidence_chart`

Operational route:

- `health`

Important helper groups:

- `_dataset_path`, `_load_dataset_view_for_id`, and `_load_profile` safely
  resolve current dataset state.
- `_report_assets` and `_current_report_package` rebuild and fingerprint the
  report dependency graph.
- `_workspace_directories` and `_workspace_resume_url` connect progress
  summaries to the correct browser continuation point.
- `_current_package_sha256` labels saved report versions as current or
  historical without preventing history access.
- `_render_generated_report` shares current and read-only historical
  rendering while `_historical_report_chart_paths` only resolves chart names
  retained for the saved report.
- `_save_generated_report_with_charts` rolls back a newly written report JSON
  if its version-specific chart snapshot cannot be persisted.
- `_render_*`, `_default_*_form`, and `_*_form_from_*` prepare templates.
- `_redirect_with_state`, `_load_view_state`, and `_state_*` implement stable
  GET pages after form submissions.
- `_generated_report_chart_paths`, `_generated_report_sections`, and
  `_generated_story_sections` prepare report presentation.

Route behaviour is covered across the route test files, especially
`tests/test_report_configuration_routes.py`,
`tests/test_visualization_builder.py`, `tests/test_navigation_routes.py`, and
`tests/test_suggestion_routes.py`.

## Templates and browser JavaScript

- `upload.html` — initial dataset upload.
- `workspaces.html` / `workspace.html` — persistent workspace index and detail.
- `sheet_selection.html` — explicit XLSX worksheet selection.
- `preview.html` — profile, suggestions, and initial KPI configuration.
- `derived_configuration.html` — derived formula preview and configuration.
- `configuration.html` — saved KPI registry.
- `insights.html` — deterministic insights and evidence cards.
- `visualization_builder.html` — manual chart editor.
- `visualization.html` / `visualizations.html` — saved chart detail/list.
- `report_configuration_form.html` — report content selection.
- `report_configuration.html` — saved report readiness and package review.
- `generated_report.html` — executive summary, published stories, evidence,
  version controls, JSON links, and PDF link.
- `report_history.html` — immutable report runs, exact versions, freshness,
  and historical HTML/JSON/PDF links.
- `_dataset_context.html` — shared context-panel partial.
- `static/context_panel.js` — safe insertion of approved context tokens.
- `static/report_configuration.js` — synchronizes KPI, evidence, and
  visualization dependencies in the report form.

Templates render already validated objects, but still rely on Jinja escaping;
they must not mark user or model text as safe HTML.
