# AI Insight Reporter

AI Insight Reporter is a local-first proof of concept for turning raw business data into
evidence-grounded reports. Python will perform deterministic calculations, and a local Ollama
model provides optional configuration suggestions before later generating narrative text from
verified evidence.

The project currently implements **Milestone 4B: Manual Visualization Builder**. It profiles one
securely ingested CSV, flat JSON dataset, or selected XLSX worksheet; supports one to five source
or derived KPIs; optionally asks local Ollama for advisory configuration suggestions; performs
every KPI calculation and insight in Python; turns each insight into reviewer-verifiable automatic
evidence; and lets users configure, review, and save validated KPI or supplementary charts for the
final report.

## Current capabilities

- Flask application factory and `/health` endpoint
- One-file upload form at `/`
- Strict UTF-8 and UTF-8-BOM CSV decoding
- Flat JSON arrays of record objects with sparse-key normalization
- XLSX content validation with explicit visible-worksheet selection
- Typed Excel numbers, booleans, and dates normalized without model involvement
- Rejection of nested JSON, duplicate JSON keys, Excel formulas, macros, external links,
  encrypted archives, unsafe XML declarations, and archive bombs
- Configurable 10 MiB, 5,000-data-row, and 200-column limits
- Randomized internal filenames under the non-public `instance/uploads/` directory
- Empty, binary, malformed, inconsistent, and duplicate-column rejection
- Escaped preview of the first five data rows
- SHA-256 source-file hash for traceability
- Automatic cleanup of failed uploads
- Deterministic row and column counts
- Numeric, categorical, date/time, boolean, identifier, free-text, and empty classifications
- Missing-value and unique-value counts
- Numeric minimum, maximum, mean, median, total, and population standard deviation
- Earliest and latest date/time values
- Constant and empty-column flags
- Candidate KPI, date, and category columns
- Optional one-to-three configuration suggestions from local Ollama
- JSON-schema-constrained model responses at temperature zero
- Python rejection of hallucinated columns, extra fields, duplicate suggestions, and invented targets
- Model confidence and evidence-based rationale displayed as advisory information
- Select-and-edit flow that prefills the existing manual configuration form
- Graceful manual fallback when Ollama or the configured model is unavailable
- User confirmation of KPI direction, dimensions, target, and business objective
- On-demand generation of at most two derived-KPI options that never runs automatically
- Suggested date, category, KPI direction, dataset-mean benchmark, and business objective for each
  derived KPI
- Source-aware `DatasetView`, `SourceManifest`, and `ColumnReference` contracts
- Restricted multi-variable formulas using bracketed numeric columns and approved functions
- Python preview of valid, missing, zero-division, and non-finite derived results
- Row formulas plus aggregate formulas such as `SUM([profit]) / SUM([revenue])`
- Manual derived-KPI builder with editable formula, calculation level, aggregation, display format,
  dimensions, benchmark, direction, role, and objective
- User confirmation before a derived KPI becomes active
- One-to-five KPI registry with one primary KPI and optional additional KPIs
- Per-KPI direction and optional target or benchmark
- Ability to change the primary KPI or remove a non-primary KPI
- Version-4 source-table-aware configuration with backward-compatible version-1 through version-3
  loading
- Validated JSON configuration under `instance/configurations/`
- Python-only missing-data warnings and explicit analysis-skip warnings
- Deterministic period-over-period KPI change and linear trend observations
- Top/bottom segment rankings and reconciled segment contributions to change
- Tukey 1.5-IQR anomaly detection
- Pearson correlations labelled strictly as associations
- Row-level target or benchmark breach counts and percentages
- Explicit handling for small samples, missing dates, zero denominators, and constant columns
- Reproducible deterministic insight JSON under `instance/insights/`
- One stable evidence ID and evidence record for every deterministic insight
- Source filename, format, SHA-256 hash, and selected Excel worksheet in every evidence record
- KPI definition, source columns, filters, periods, calculation description, and supporting table
- Reproducible impact, confidence, relevance, combined-score, and rank values
- Automatic time-trend, category-comparison, segment-contribution, IQR-distribution, and
  missing-data charts
- Randomized chart filenames and validated chart serving from non-public `instance/charts/`
- Versioned evidence JSON under `instance/evidence/`
- Evidence generation for CSV, JSON, XLSX, source KPIs, derived KPIs, and one-to-five KPI registries
- Manual visualization builder with preview, save, reopen, edit, and regeneration flows
- Report-selectable KPI visualizations and clearly labelled supplementary visualizations
- Supplementary numeric-column and record-count measures that do not become configured KPIs
- Time-series line, vertical/horizontal category bar, scatter, histogram, and box charts
- Day, month, quarter, and year grouping for time-series visualizations
- Structured date/category filters, include/exclude modes, sorting, Top-N, bin count, and scale
- One-to-five compatible measures on line and category charts
- Recalculation of aggregate-formula KPIs within each displayed period or category
- Secure versioned visualization JSON under `instance/visualizations/`
- Short-lived validated preview artifacts under `instance/visualization_previews/`
- Stable dataset-context tokens for configured KPIs, numeric columns, categories, booleans, dates,
  saved visualizations, and evidence
- Dataset-context panels beside the formula editor and business-objective input, with safe insert
  buttons
- Localhost-only and debug-off defaults

Ollama is optional: dataset upload, profiling, manual configuration, derived formulas,
deterministic insights, automatic evidence, and the manual visualization builder continue to work
without it.

## Setup with the existing Conda environment

```bash
conda activate ollama-env
cd "/Users/mrunal/Documents/Projects/ollama project"
python -m pip install -r requirements-dev.txt
```

Using Conda is supported; a separate `.venv` is not required.

## Prepare local Ollama suggestions

No API key is required. Install and start the local Ollama application or service, then download
the configured model:

```bash
ollama serve
ollama pull llama3.2:latest
```

If the desktop application already runs Ollama in the background, do not start a second server.
The Flask application connects only to `http://127.0.0.1:11434`.

## Run the dataset upload service

```bash
python -m flask --app insight_reporter:create_app run --host 127.0.0.1 --port 5000
```

Open `http://127.0.0.1:5000/` to upload a CSV, JSON, or XLSX source and review its deterministic
profile. For a workbook with multiple visible worksheets, choose one worksheet before profiling.
Click
**Generate AI suggestions** only when suggestions are wanted. Select **Use this suggestion** to
prefill the manual form, review or edit every field, and then confirm the final configuration.
Existing numeric columns always remain selectable. Click **Suggest two derived KPIs** for two
formula-plus-configuration options, or **Build a derived KPI manually** to start without Ollama.
Edit the formula or configuration fields and recalculate the Python preview before confirming it.
The saved configuration page can change the primary KPI and each KPI's direction or benchmark.
From the saved-configuration page, select **Generate deterministic insights** to run the Python-only
engine and review the ranked evidence cards, supporting tables, charts, and JSON artifacts.
Select **Build a manual visualization** to configure an additional chart from validated fields and
settings. Previewed charts are not retained until explicitly saved. A saved supplementary chart may
be included in the final report, but remains labelled as supplementary rather than KPI evidence.

All form actions use POST/Redirect/GET. Dataset profiles, suggestion results, formula previews,
saved configurations, validation messages, and insight reports therefore finish on stable GET URLs,
so browser Back, Forward, and Reload do not repeat a form submission. Small UI-only state is stored
outside the static directory for 24 hours; raw dataset rows are never placed in state URLs.

The health endpoint is available at `http://127.0.0.1:5000/health`.

```text
CSV / flat JSON / selected XLSX worksheet
                                  -> Python validation/profile
                                  -> optional Ollama suggestions
                                  -> Python validation and user review/edit
                                  -> source-aware KPI configuration
              optional derived KPI suggestion -> restricted Python validation/preview
                                              -> user confirmation
                                              -> derived KPI configuration
                                  -> Python deterministic insight engine
                                  -> deterministic insight JSON
                                  -> ranked evidence records and Python charts
                                  -> optional manual KPI/supplementary visualization
                                  -> Python validation/calculation/chart preview
                                  -> user-confirmed final-report inclusion
```

Confirm the expected routes with:

```bash
python -m flask --app insight_reporter:create_app routes
```

Expected application routes:

```text
GET   /
GET   /dataset/<dataset_id>
GET   /dataset/<dataset_id>/sheet
GET   /derived/<dataset_id>
GET   /configuration/<dataset_id>
GET   /insights/<dataset_id>
GET   /evidence/<dataset_id>/<evidence_id>/chart
GET   /visualizations/<dataset_id>
GET   /visualizations/<dataset_id>/new
GET   /visualizations/<dataset_id>/preview/<token>
GET   /visualizations/<dataset_id>/preview/<token>/chart
GET   /visualizations/<dataset_id>/<visualization_id>
GET   /visualizations/<dataset_id>/<visualization_id>/chart
GET   /health
POST  /upload
POST  /dataset/<dataset_id>/sheet
POST  /suggest/<dataset_id>
POST  /review-suggestion/<dataset_id>
POST  /suggest-derived/<dataset_id>
POST  /review-derived/<dataset_id>
POST  /configure/<dataset_id>
POST  /configure-derived/<dataset_id>
POST  /configuration/<dataset_id>/primary
POST  /configuration/<dataset_id>/metric
POST  /configuration/<dataset_id>/remove
POST  /insights/<dataset_id>
POST  /visualizations/<dataset_id>/preview
POST  /visualizations/<dataset_id>/preview/<token>/save
POST  /visualizations/<dataset_id>/<visualization_id>/regenerate
```

## Derived KPI rules

New users should begin with the worked examples and decision guide in
[Derived KPI Formula Guide](FORMULA_GUIDE.md).

- Derived KPI suggestions are optional; the model is never forced to derive a KPI.
- Ollama returns at most two derived options. Each can include an applicable date, categories,
  direction, dataset-mean benchmark strategy, and business objective.
- At least two numeric columns are required before Ollama can suggest a formula. The manual builder
  needs only one numeric column.
- Ollama receives compact numeric metadata only, not raw rows or preview values. The prompt is
  limited to the first 40 non-constant, non-identifier numeric columns so it fits the local model's
  context window without losing its JSON instructions.
- Ollama's current suggestions are deliberately simple two-column starting points. During review,
  they are migrated to the same flexible formula format used by the manual builder.
- Python creates a literal name from the selected columns and operation; the user can rename it
  during review. The model cannot insert an unsupported or misleading KPI name.
- A row formula may reference up to 20 numeric columns using `+`, `-`, `*`, `/`, parentheses, and
  `ABS(...)`. Column names use exact bracket tokens such as `[gross revenue]`.
- An aggregate formula wraps every column in `SUM`, `MEAN`, `MEDIAN`, `MIN`, `MAX`, or `COUNT`.
  This supports mathematically correct ratios of aggregates.
- Formula text is parsed into a bounded expression tree. It is never passed to `eval`, `exec`, a
  shell, or the language model for calculation.
- Numeric benchmark values are never invented by Ollama. When suggested, Python pre-fills the
  derived KPI's reproducible dataset mean, and the user may edit or remove it.
- Row-formula results support sum, mean, median, minimum, or maximum aggregation. Aggregate
  formulas evaluate their explicit aggregate functions directly.
- Missing inputs, division by zero, and non-finite results become `null` and are counted in the
  preview; they are never guessed or replaced by the model.
- Percentage calculations and every downstream insight are calculated by Python.
- Additive derived KPIs can produce contribution-to-change insights. Mean and ratio KPIs skip that
  analysis because their segment changes cannot be reconciled as additive contributions.
- Every evidence report stores the derived formula, aggregation, display format, null policies, and
  actual source columns.

## Automatic evidence and chart rules

- Evidence IDs are deterministic hashes of immutable dataset and insight identity fields. Chart
  filenames are separately randomized and never derived from uploaded filenames or labels.
- Each deterministic insight has exactly one evidence record, including warnings and analyses that
  were skipped safely.
- Evidence records retain the safe internal source filename, format, full SHA-256 hash, and selected
  worksheet for XLSX sources.
- Supporting data comes from the deterministic insight output or from the exact row-level values
  used by its Python calculation. Correlations include sufficient statistics that reproduce the
  Pearson coefficient.
- Impact, confidence, and relevance use documented Python scoring rules on a zero-to-one scale.
  The combined score is `0.5 × impact + 0.3 × confidence + 0.2 × relevance`; rank ties use the
  stable evidence ID.
- Time trends visualize period/value evidence; category comparisons visualize segment rankings;
  contribution charts visualize segment changes; box plots visualize the exact IQR input values;
  and the missing-data overview uses profiled missing counts.
- Empty or skipped results do not create placeholder or broken charts.
- Matplotlib uses its non-interactive `Agg` backend. Chart labels have control characters removed,
  math-text delimiters escaped, and length limited before rendering.
- Charts and evidence are outside Flask's static directory. A chart is served only when its safe
  basename is referenced by the requested dataset and evidence record.
- Regenerating evidence atomically replaces its JSON and removes only the old chart files referenced
  by the previous evidence artifact.

## Visualization validation rules

- A chart may use configured KPIs, non-constant numeric source columns, or record count.
- A chart containing any unconfigured measure is stored as a `supplementary` visualization. It may
  be selected for the final report but is not converted into a KPI, deterministic insight, or
  evidence claim.
- Time-series charts require a recognized date column. Category charts require a recognized
  category column. Scatter plots require a numeric x-axis and exactly one row-level measure.
- Histograms and box plots accept one source or row-derived measure. Aggregate-formula KPIs are
  rejected for row-level charts because they do not have one value per source row.
- Aggregate-formula KPIs are recalculated from the applicable rows in every period or category.
- Multiple measures must share a display format. Dual-axis charts are deliberately unsupported.
- Structured category filters may include or exclude up to 50 exact values. Date filters require
  valid ISO start/end dates, Top-N is limited to 1–50, and series grouping is limited to 12 values.
- Logarithmic scale is rejected when any plotted value is zero or negative and is not offered for
  histogram or box charts.
- Every preview and saved chart includes source hash/format/worksheet, measure definitions,
  filters, axes, aggregation, supporting data, and a randomized chart filename.
- Drafts use unguessable preview tokens and expire after 24 hours. Saved charts receive stable
  `VIS-...` identifiers.
- Manual visualization generation never calls Ollama.

## Deterministic calculation rules

- Existing source-column KPIs use sum aggregation for period and segment calculations. Confirmed
  derived KPIs use their validated row-result aggregation or explicit aggregate formula.
- Every configured KPI is analyzed independently and every insight includes its stable metric ID.
- Calendar months are used when data spans multiple months; otherwise calendar days are used.
- Period change requires two comparison periods with at least two valid KPI records each.
- Trend requires at least three eligible periods and is descriptive, not causal.
- Segment rankings exclude segments with fewer than two valid KPI records.
- Contribution percentages use every categorized segment in the last two periods and are adjusted
  only for floating-point rounding so they reconcile to exactly 100%. If overall change is zero,
  percentages are explicitly `null`.
- Anomalies use inclusive quartiles and Tukey's 1.5-IQR fences with at least four valid records.
- Correlations use pairwise-complete Pearson coefficients with at least three pairs and are always
  labelled as associations.
- Benchmark breaches are evaluated per non-missing KPI row according to whether higher or lower is
  configured as better.
- Missing configured dates cause temporal analysis to be skipped; dates are never imputed.

## Test and lint

```bash
python -m pytest
python -m ruff check .
```

## Configuration

The application reads these optional process environment variables:

| Variable | Default | Purpose |
|---|---:|---|
| `APP_LOG_LEVEL` | `INFO` | Application logging level |
| `APP_SECRET_KEY` | generated per process | Local Flask signing key |
| `APP_MAX_UPLOAD_BYTES` | `10485760` | Maximum CSV, JSON, or XLSX file size |
| `APP_MAX_CSV_ROWS` | `5000` | Maximum data rows for every supported format |
| `APP_MAX_CSV_COLUMNS` | `200` | Maximum columns for every supported format |
| `APP_CSV_PREVIEW_ROWS` | `5` | Rows displayed after validation |
| `APP_OLLAMA_MODEL` | `llama3.2:latest` | Local model used only for suggestions |
| `APP_OLLAMA_TIMEOUT_SECONDS` | `120` | Local suggestion request timeout |

The `APP_MAX_CSV_ROWS`, `APP_MAX_CSV_COLUMNS`, and `APP_CSV_PREVIEW_ROWS` names are retained for
backward compatibility, but their limits now apply equally to CSV, JSON, and XLSX inputs.

The application does not automatically load `.env`. Never commit real secrets or sensitive input
data.

## Security notes

- Client filenames and MIME types are not trusted for validation or storage naming.
- CSV and JSON content must decode strictly as UTF-8 and may not contain unsafe control bytes.
- File format is detected from content; client filenames and MIME types do not select the parser.
- JSON must be a top-level array of flat objects. Nested values and duplicate keys are rejected.
- XLSX files are inspected as bounded ZIP containers. Macros, external workbook links, encrypted
  members, formulas, unsafe XML declarations, and suspicious compression ratios are rejected.
- Rejected and incomplete uploads are deleted.
- Uploaded files are stored outside Flask's public static directory.
- Preview values are HTML-escaped by Jinja.
- Configuration selections are checked against inferred candidates and actual column names.
- Configuration filenames are derived only from server-generated dataset IDs.
- Saved configurations are revalidated against the retained dataset and SHA-256 hash before use.
- Version-1 through version-3 configurations remain loadable and are migrated in memory; new
  configurations use version 4.
- Persisted formulas include a source-qualified expression tree and are reparsed and compared when
  loaded, so tampering is rejected.
- Insight calculations never call Ollama and never delegate arithmetic to a language model.
- Insight files are stored outside the static directory and contain source-column traceability.
- Ollama visualization assistance receives stable tokens, KPI definitions, descriptive statistics,
  bounded category values, and date ranges—not raw preview rows, free-text cells, or source rows.
- Column names are treated as untrusted data in the prompt and model output is treated as untrusted.
- Structured output is validated again in Python before suggestions are displayed.
- The model-facing JSON grammar stays simple for `llama3.2` while restricting KPI,
  date, and category selections to profiler candidates; Python enforces the remaining constraints.
- Ollama suggestions cannot set a target or benchmark; only the user can enter one.
- Derived suggestions contain definitions only. Python rejects invented columns, unknown
  operations, inconsistent percentage formats, duplicate dataset-column names, and unsafe policies.
- AI suggestions are never generated automatically during upload and never become final automatically.
- The application does not execute uploaded content or use `eval`, `exec`, pickle, or shell commands.
- Flask and Ollama must remain bound to loopback unless company IT approves another design.
- Use only dummy data until company data handling and retention are approved.

## Optional standalone Ollama check

After Ollama is installed, running, and has an approved model downloaded:

```bash
python app.py
```

This standalone check is optional and separate from the Flask workflow.

## Current limitations

- One source is accepted per upload. Supporting several files and explicit joins remains a later
  milestone; joins are never guessed.
- JSON is limited to a top-level array of flat objects.
- Excel support is limited to `.xlsx`; legacy `.xls`, macro-enabled workbooks, formulas, hidden-only
  workbooks, and password-protected files are rejected.
- Exactly one visible worksheet is analyzed from an Excel workbook.
- Type inference is heuristic and must be confirmed by the user.
- Empty strings and the markers `NA`, `N/A`, `null`, `none`, and `NaN` count as missing.
- Derived formulas do not yet support joins, rolling windows, forecasting, conditionals, or custom
  code.
- AI confidence is advisory and not a calibrated probability.
- Suggestions depend on the semantic ability of the selected local model and require human review.
- No Ollama report narration in the Flask workflow
- No report generation
- No persistent history, authentication, or multi-user isolation
