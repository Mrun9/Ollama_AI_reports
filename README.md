# AI Insight Reporter

AI Insight Reporter is a local-first proof of concept for turning raw business data into
evidence-grounded reports. Python will perform deterministic calculations, and a local Ollama
model provides optional configuration suggestions before later generating narrative text from
verified evidence.

The project currently implements **Milestone 3.5: Optional Derived KPIs**. It profiles one securely
ingested CSV, keeps existing numeric columns available as the default KPI choices, optionally asks
local Ollama for configuration or derived-KPI suggestions, and performs all calculations in Python.

## Current capabilities

- Flask application factory and `/health` endpoint
- One-file upload form at `/`
- Strict UTF-8 and UTF-8-BOM CSV decoding
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
- Restricted two-column derived formulas using approved arithmetic and ratio operations
- Python preview of valid, missing, zero-division, and non-finite derived results
- Manual derived-KPI builder with editable name, source columns, operation, aggregation, display
  format, dimensions, benchmark, direction, and objective
- User confirmation before a derived KPI becomes active
- Version-2 JSON configuration with backward-compatible version-1 loading
- Validated JSON configuration under `instance/configurations/`
- Python-only missing-data warnings and explicit analysis-skip warnings
- Deterministic period-over-period KPI change and linear trend observations
- Top/bottom segment rankings and reconciled segment contributions to change
- Tukey 1.5-IQR anomaly detection
- Pearson correlations labelled strictly as associations
- Row-level target or benchmark breach counts and percentages
- Explicit handling for small samples, missing dates, zero denominators, and constant columns
- Reproducible evidence JSON under `instance/insights/`
- Localhost-only and debug-off defaults

Ollama is optional: CSV upload, profiling, manual configuration, and deterministic insight
generation continue to work without it.

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

## Run the CSV upload service

```bash
python -m flask --app insight_reporter:create_app run --host 127.0.0.1 --port 5000
```

Open `http://127.0.0.1:5000/` to upload a CSV and review its deterministic profile. Click
**Generate AI suggestions** only when suggestions are wanted. Select **Use this suggestion** to
prefill the manual form, review or edit every field, and then confirm the final configuration.
Existing numeric columns always remain selectable. Click **Suggest two derived KPIs** for two
formula-plus-configuration options, or **Build a derived KPI manually** to start without Ollama.
Edit any structured formula or configuration field and recalculate the Python preview before
confirming it.
From the saved-configuration page, select **Generate deterministic insights** to run the Python-only
engine and review its evidence JSON.

All form actions use POST/Redirect/GET. Dataset profiles, suggestion results, formula previews,
saved configurations, validation messages, and insight reports therefore finish on stable GET URLs,
so browser Back, Forward, and Reload do not repeat a form submission. Small UI-only state is stored
outside the static directory for 24 hours; raw CSV rows are never placed in state URLs.

The health endpoint is available at `http://127.0.0.1:5000/health`.

```text
CSV -> Python validation/profile -> optional Ollama suggestions
                                  -> Python validation
                                  -> user review/edit
                                  -> source KPI configuration
              optional derived KPI suggestion -> restricted Python validation/preview
                                              -> user confirmation
                                              -> derived KPI configuration
                                  -> Python deterministic insight engine
                                  -> validated evidence JSON
```

Confirm the expected routes with:

```bash
python -m flask --app insight_reporter:create_app routes
```

Expected application routes:

```text
GET   /
GET   /dataset/<dataset_id>
GET   /derived/<dataset_id>
GET   /configuration/<dataset_id>
GET   /insights/<dataset_id>
GET   /health
POST  /upload
POST  /suggest/<dataset_id>
POST  /review-suggestion/<dataset_id>
POST  /suggest-derived/<dataset_id>
POST  /review-derived/<dataset_id>
POST  /configure/<dataset_id>
POST  /configure-derived/<dataset_id>
POST  /insights/<dataset_id>
```

## Derived KPI rules

- Derived KPI suggestions are optional; the model is never forced to derive a KPI.
- Ollama returns at most two derived options. Each can include an applicable date, categories,
  direction, dataset-mean benchmark strategy, and business objective.
- At least two numeric columns are required before Ollama can suggest a formula.
- Ollama receives compact numeric metadata only, not CSV rows or preview values. The prompt is
  limited to the first 40 non-constant, non-identifier numeric columns so it fits the local model's
  context window without losing its JSON instructions.
- A formula contains exactly two real numeric columns and one approved operation: addition,
  subtraction, multiplication, ratio, percentage ratio, percentage difference, or margin percent.
- Python creates a literal name from the selected columns and operation; the user can rename it
  during review. The model cannot insert an unsupported or misleading KPI name.
- Users can replace either source column, change the approved operation, aggregation, and display
  format, or build a formula manually. Python revalidates and recalculates the preview after edits.
- Numeric benchmark values are never invented by Ollama. When suggested, Python pre-fills the
  derived KPI's reproducible dataset mean, and the user may edit or remove it.
- Formula text is generated by Python from structured fields; free-form expressions are not
  accepted or executed.
- Supported aggregations are sum, mean, and ratio of sums. Ratio of sums is restricted to ratio and
  percentage operations.
- Missing inputs, division by zero, and non-finite results become `null` and are counted in the
  preview; they are never guessed or replaced by the model.
- Percentage calculations and every downstream insight are calculated by Python.
- Additive derived KPIs can produce contribution-to-change insights. Mean and ratio KPIs skip that
  analysis because their segment changes cannot be reconciled as additive contributions.
- Every evidence report stores the derived formula, aggregation, display format, null policies, and
  actual source columns.

## Deterministic calculation rules

- Existing source-column KPIs use sum aggregation for period and segment calculations. Confirmed
  derived KPIs use their validated sum, mean, or ratio-of-sums aggregation.
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
| `APP_MAX_UPLOAD_BYTES` | `10485760` | Maximum CSV file size |
| `APP_MAX_CSV_ROWS` | `5000` | Maximum data rows, excluding the header |
| `APP_MAX_CSV_COLUMNS` | `200` | Maximum columns |
| `APP_CSV_PREVIEW_ROWS` | `5` | Rows displayed after validation |
| `APP_OLLAMA_MODEL` | `llama3.2:latest` | Local model used only for suggestions |
| `APP_OLLAMA_TIMEOUT_SECONDS` | `120` | Local suggestion request timeout |

The application does not automatically load `.env`. Never commit real secrets or sensitive input
data.

## Security notes

- Client filenames and MIME types are not trusted for validation or storage naming.
- Uploaded content must decode strictly as UTF-8 CSV and may not contain binary control bytes.
- Rejected and incomplete uploads are deleted.
- Uploaded files are stored outside Flask's public static directory.
- Preview values are HTML-escaped by Jinja.
- Configuration selections are checked against inferred candidates and actual column names.
- Configuration filenames are derived only from server-generated dataset IDs.
- Saved configurations are revalidated against the retained dataset and SHA-256 hash before use.
- Version-1 source configurations remain loadable; new source and derived configurations use
  version 2.
- Insight calculations never call Ollama and never delegate arithmetic to a language model.
- Insight files are stored outside the static directory and contain source-column traceability.
- Ollama receives column metadata, descriptive statistics, and candidate lists—not raw preview rows.
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

- No JSON upload
- Type inference is heuristic and must be confirmed by the user.
- Empty strings and the markers `NA`, `N/A`, `null`, `none`, and `NaN` count as missing.
- Derived formulas currently support exactly two numeric source columns; nested formulas, joins,
  rolling windows, forecasting, and custom code are not supported.
- AI confidence is advisory and not a calibrated probability.
- Suggestions depend on the semantic ability of the selected local model and require human review.
- No Ollama report narration in the Flask workflow
- No report generation
- No persistent history, authentication, or multi-user isolation
