# AI Insight Reporter

AI Insight Reporter is a local-first proof of concept for turning raw business data into
evidence-grounded reports. Python performs deterministic calculations, and a local Ollama model
provides optional configuration suggestions and evidence-grounded narrative wording from verified
Python facts.

New to the codebase? Read [Project Architecture](ARCHITECTURE.md) first. It
contains the end-to-end request flow, module responsibilities, runtime artifact
map, trust boundaries, and the correct file to edit for common changes. The
sections below are the detailed behavior and safety reference.

## Documentation map

The documentation is organised for two audiences: someone presenting the
project and someone continuing its development.

| Document | Use it for |
| --- | --- |
| This README | Project story, milestone history, setup, current behaviour, and limitations |
| [Project Architecture](ARCHITECTURE.md) | Short technical orientation and module map |
| [Module and Function Reference](docs/MODULE_REFERENCE.md) | Responsibilities, public APIs, important internal functions, and related tests |
| [User Input and Artifact Flow](docs/USER_INPUT_TO_OUTPUT.md) | How browser input becomes validated files, evidence, AI narration, HTML, JSON, and PDF |
| [Derived KPI Formula Guide](FORMULA_GUIDE.md) | Formula syntax, calculation levels, examples, and troubleshooting |

## The project story: how each milestone changed the system

The project did not begin as an unrestricted “ask AI about a spreadsheet”
application. Its central design decision was the opposite: **Python calculates
facts; Ollama may suggest configuration or write prose only after those facts
exist**. Each milestone added one layer while preserving that boundary.

### Milestone 1 — Secure single-CSV ingestion

**Problem:** Before analysing data, the application needed a safe and
repeatable way to accept an untrusted file.

**Implemented:**

- A Flask application factory and localhost-only server defaults.
- A bounded upload route with randomized internal filenames.
- Strict CSV decoding, row-width checks, duplicate-header rejection, size and
  shape limits, and cleanup of rejected uploads.
- A SHA-256 source fingerprint and an escaped preview.

**Result:** The application could retain one CSV without trusting its filename,
MIME type, contents, or browser-supplied metadata. This established the
dataset ID used by every later artifact.

### Milestone 2 — Deterministic profiling and business configuration

**Problem:** Raw columns needed understandable types and statistics before a
user could define a KPI.

**Implemented:**

- Numeric, categorical, date/time, boolean, identifier, free-text, and empty
  column classification.
- Missingness, uniqueness, numeric statistics, and date ranges.
- Candidate KPI, date, and category fields.
- A reviewed business configuration containing the KPI direction, dimensions,
  optional benchmark, and business objective.

**Result:** The system could explain the dataset and save a validated analysis
intent without using a language model.

### Milestone 2.5 — AI-assisted configuration

**Problem:** Users might not know which columns or settings make useful KPIs,
but the model must not silently configure the analysis.

**Implemented:**

- Optional local Ollama suggestions constrained by JSON Schema.
- Compact profile metadata instead of raw dataset rows.
- Python rejection of unknown columns, duplicate suggestions, extra fields,
  and invented targets.
- A review-and-edit screen before any suggestion becomes configuration.

**Result:** AI became an adviser, not the owner of the analysis. Manual
configuration continued to work when Ollama was unavailable.

### Milestone 3A — Deterministic insight generation

**Problem:** A configured KPI was not useful until the application could
calculate reproducible findings.

**Implemented:**

- Missing-data and insufficient-data warnings.
- Period change and linear trend calculations.
- Segment rankings and segment contributions.
- Tukey IQR anomaly detection.
- Pearson correlations labelled as associations.
- Benchmark-breach counts and percentages.
- Stable insight JSON saved under `instance/insights/`.

**Result:** Every numerical observation came from Python and could be tested
without Ollama.

### Milestone 3.5 — Optional derived KPIs

**Problem:** Important business measures are often formulas rather than source
columns.

**Implemented:**

- Optional Ollama-derived KPI suggestions.
- A manual derived-KPI editor.
- Restricted arithmetic and ratio definitions.
- Python previews for valid values, missing inputs, zero division, and
  non-finite results.
- User confirmation before a derived KPI enters the KPI registry.

**Result:** The application could analyse calculated business measures while
keeping formula execution deterministic.

### Milestone 3.7 — Multi-format ingestion and formula engine

**Problem:** The original CSV-specific path and two-column formulas were too
limited for realistic single-dataset work.

**Implemented:**

- One `DatasetView` abstraction for CSV, flat JSON, and one selected XLSX
  worksheet.
- Safe JSON and XLSX validation, including rejection of formulas, macros,
  external links, unsafe archives, and unsupported nested data.
- A one-to-five KPI registry with source and derived KPIs.
- A restricted parser supporting multi-column row formulas and aggregate
  formulas such as `SUM([profit]) / SUM([revenue])`.

**Result:** Downstream profiling, insights, evidence, and charts stopped caring
which supported file format supplied the rows.

### Milestone 4A — Evidence records and automatic charts

**Problem:** An insight value alone was not enough for a reviewer to understand
or verify a finding.

**Implemented:**

- One stable evidence record per deterministic insight.
- Calculation descriptions, source columns, filters, periods, record counts,
  supporting rows, limitations, and source fingerprints.
- Deterministic relevance, confidence, impact, combined score, and rank.
- Automatic charts appropriate to time, category, distribution, contribution,
  missingness, and association findings.

**Result:** Findings became traceable objects rather than isolated sentences.
The later AI boundary could now refer to evidence IDs and exact fact paths.

### Milestone 4B — Manual visualization builder

**Problem:** Automatic evidence charts cannot answer every user question.

**Implemented:**

- Preview, validate, save, reopen, edit, and regenerate flows.
- Time series, category bars, scatter plots, histograms, and box plots.
- Filters, aggregation, grouping, sorting, Top-N, scales, and multiple
  compatible measures.
- KPI-backed and supplementary chart classifications.
- Deterministic evidence derived from the values actually displayed.

**Result:** Users could add report-ready charts without allowing the model to
write plotting code or calculate chart values.

### Milestone 5A.1 — Report selection and bounded package

**Problem:** Not every KPI, evidence record, or chart should be sent to report
generation.

**Implemented:**

- A report-configuration form for title, objective, audience, tone, detail,
  notes, KPI selection, evidence selection, and chart selection.
- Validation that evidence belongs to selected KPIs and charts remain
  reproducible.
- Fingerprints binding the report selection to its configuration, evidence,
  source, and visualization definitions.
- A bounded, inspectable JSON package with no raw rows or row identifiers.

**Result:** The exact future model input became reviewable before Ollama was
called.

### Milestone 5B.1 — Evidence-grounded multi-story narration

**Problem:** Report prose needed to combine related evidence without inventing
facts or changing calculated values.

**Implemented:**

- Stable story packs grouping related evidence for one metric.
- Structured headline, finding, interpretation, next-step, caveat, and fact
  reference fields.
- Exact-value validation for every number in model prose.
- Rejection and retry of unknown facts, rounded numbers, duplicate references,
  invalid scope, unsupported units, and causal wording.
- Deterministic fallback stories when an individual response remains invalid.

**Result:** Ollama could write useful narrative while Python retained control of
scope, provenance, and numerical truth.

### Milestone 5C — Final composition and export

**Problem:** Validated stories still needed to become a publishable artifact.

**Implemented:**

- Immutable, versioned generated-report JSON.
- HTML report composition with story sections, KPI overview, evidence links,
  charts, limitations, source traceability, and an optional appendix.
- Story inclusion and ordering controls.
- Single-story regeneration without replacing other valid stories.
- Print-ready **PDF export**, retained as a core output alongside HTML and JSON.

**Result:** The full path from dataset upload to a downloadable,
evidence-grounded report was complete.

### Milestone 5C hardening — Precision, executive summary, and maintainability

**Problem:** Early model responses could be vague or fail validation silently,
and the growing codebase had become difficult to explain.

**Implemented:**

- Validation-aware story retries and explicit zero-AI failure handling.
- More precise story instructions separating finding, interpretation, action,
  and caveat.
- Exactly five prioritized executive-summary points, each scoped to validated
  stories and Python facts.
- Evidence-strength confidence labels and explanations.
- Removal of the obsolete CSV-only ingestion implementation and unused legacy
  insight reader.
- A project architecture guide and the detailed developer references linked
  above.

**Result:** New reports communicate the most important findings more clearly,
and invalid AI output is visible rather than being mistaken for successful
generation.

### Milestone 6A — Persistent workspace and report history

**Problem:** Runtime artifacts survived locally, but users had no reliable
index for finding an earlier dataset, understanding how far its workflow had
progressed, or reopening an exact report revision.

**Implemented:**

- Versioned workspace metadata created for every new upload, including a safe
  human-readable name, original filename, internal dataset identity, format,
  source fingerprint, size, and creation time.
- A `/workspaces` index reconstructed from retained source and downstream
  artifacts rather than an application database.
- A workspace detail page with rename support, current workflow stage, last
  activity, report-run counts, and a stage-aware resume action.
- Compatibility entries for datasets uploaded before Milestone 6A; renaming
  one materializes its safe versioned workspace metadata.
- A complete report-history page that distinguishes independent generation
  runs from immutable revisions.
- Version-specific chart snapshots so regenerating upstream evidence cannot
  remove visuals from an already saved report.
- Exact-version HTML, JSON, and PDF routes. Historical reports can be reopened
  even when their source package no longer matches the current configuration.
- Current-package fingerprint labels that distinguish current reports from
  historical snapshots.

**Result:** Closing the application no longer makes earlier work difficult to
find. A user can reopen a dataset, continue its workflow, inspect every saved
report revision, and export an exact historical version without overwriting
anything.

### Milestone 6A.1 — Workspace-first project lifecycle

**Problem:** A workspace still came into existence only after a successful
upload. That made the file picker feel like the product's home page, left no
place to plan work before selecting data, and provided no lifecycle controls
for old workspaces, source labels, or report runs.

**Implemented:**

- `/` now starts at the persistent workspace index.
- A workspace can be created with a name and optional description before it
  has a source. Its server-generated identity is then reused when the one
  supported CSV, flat JSON, or XLSX source is attached.
- Empty workspaces show a source-selection action. Source-backed workspaces
  show a create-first-report or stage-aware resume action, and generated
  reports are listed directly on the workspace.
- Workspace names/descriptions, data-source display names, and report display
  names can be edited without changing safe filenames, IDs, evidence, or
  immutable report JSON.
- Workspace and report deletion is a recoverable metadata archive. Source
  deletion moves the retained source and XLSX worksheet sidecar to
  `instance/trash/sources/<dataset_id>/`.
- Restoring a source returns it to the same safe dataset identity, so all
  retained configurations and reports remain connected.
- Removing a source does not remove generated reports. Their exact saved HTML,
  JSON, and PDF versions remain available from immutable history.
- Existing upload-first datasets and schema-1 workspace metadata remain
  readable. A lifecycle edit safely adopts a source-only legacy entry into the
  current schema.

**Result:** The product now behaves like a small project workspace rather than
an upload form. A user chooses or creates the project first, then adds its
source and manages the reports that belong to it without losing history.

### Milestone 6B — Management-focused insights and AI diagnostics

**Problem:** The original five-point summary could be technically valid but
too generic for management. It did not require a concrete value, period,
segment, business implication, or next action, and row-level target breaches
could not identify which region or segment needed attention.

**Implemented:**

- Management-focused prioritization that raises target gaps, recent changes,
  and segment differences above associations and technical warnings.
- Quarter-level grouping for medium-length time series and year-level grouping
  for longer series, so changes can be reported in business-friendly periods.
- Per-segment target performance with average value, average gap, breach count,
  breach percentage, and explicit best/worst segment identification.
- A dedicated segment target-performance chart.
- Exactly five structured management points, each separating **what happened**,
  **why it matters**, and a **recommended action**.
- Required exact Python-calculated values plus verified period, quarter,
  region, segment, direction, and benchmark context. Unsupported contexts,
  rounded values, invented calculations, and causal explanations remain
  rejected.
- Python-derived `business_context` attached to every story when selected
  evidence contains a product, region, segment, cohort, channel, chart
  category, or period. The management summary must name at least one of these
  exact values whenever one is available.
- Versioned AI diagnostics recording accepted stories, deterministic
  fallbacks, rejected story packs, summary provenance, and active validation
  safeguards.
- The same structured summary and diagnostics in HTML, JSON, and PDF exports.

**Result:** The report now answers questions such as “how much did revenue
change in which quarter?” and “which region is missing its target most often?”
using verified values, then states why the result deserves attention and what
management should review next.

## Scope decisions and planned work

- **Single-dataset scope is intentional.** Multi-file joins are not planned for
  this project because safe key selection, join cardinality, and join
  validation would add substantial complexity that cannot currently be tested
  well.
- **PDF export remains a core feature.** Future formats may be additive but
  must not replace it.
- **The final visual redesign is intentionally deferred.** Functional screens
  will continue evolving while features are added; cohesive product styling
  should happen once the workflow is stable.
- **Milestone 6A — Persistent workspace and report history:** implemented.
- **Milestone 6A.1 — Workspace-first lifecycle controls:** implemented.
- **Milestone 6B — More precise insight prioritization and AI diagnostics:**
  implemented.
- **Milestone 6C — Period/cohort comparisons within one dataset:** planned;
  this deepens analysis without adding cross-dataset joins.
- **Milestone 6D — Report presentation improvements:** planned after the
  remaining functionality, while retaining HTML, JSON, and PDF exports.

The project currently implements **Milestone 6A.1: Workspace-first project
lifecycle** and **Milestone 6B: Management-focused insights and AI
diagnostics**. It profiles one
securely ingested CSV, flat JSON dataset, or selected XLSX worksheet; supports one to five source
or derived KPIs; optionally asks local Ollama for advisory configuration suggestions; performs
every KPI calculation and insight in Python; turns each insight into reviewer-verifiable automatic
evidence; lets users configure, review, and save validated KPI or supplementary charts; and creates
a source-bound selection of the trusted KPIs, evidence, and charts. It then builds a bounded,
evidence-only JSON package and uses local Ollama for structured stories whose numerical claims are
validated against Python facts. The narrative can naturally quote several verified values and add
observations, cautious interpretations, and practical suggested next steps. Published reports
support story selection and ordering, embedded charts, understandable evidence-confidence labels,
print-ready HTML, and verified PDF downloads.

### Workspace lifecycle semantics

- A workspace may exist without a source, but this project still supports
  exactly one active source per workspace.
- Source contents are immutable inside a workspace. A user may rename the
  display label or archive/restore the file; a genuinely different file should
  start a new workspace so old evidence cannot be confused with new data.
- Renaming a workspace, source, or report changes only escaped presentation
  metadata. Server-controlled IDs and filenames never change.
- A generated report is immutable. “Edit report configuration” changes the
  input for a future generation; saving or regenerating appends a version.
- Deleting a workspace or report archives its ID in workspace metadata.
  Deleting a source moves only the source and optional XLSX selection sidecar
  to recoverable trash. It deliberately keeps derived artifacts and reports.
- Restoring reverses those lifecycle markers or file moves. There is currently
  no permanent-delete UI, which avoids accidental destruction of local
  project history.

## Current capabilities

- Flask application factory and `/health` endpoint
- Workspace-first home page at `/workspaces` (with `/` redirecting there)
- Empty workspace creation before source selection
- One-file source selection inside a workspace, with `/upload` retained as a
  compatible upload-first path
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
- Versioned workspace metadata under `instance/workspaces/`
- Persistent `/workspaces` index ordered by latest artifact activity
- Safe human-readable workspace naming and read-only legacy-upload discovery
- Editable workspace descriptions, source display names, and report aliases
- Recoverable workspace/report archival and recoverable source trash
- Stage-aware source, create-report, and resume actions
- Deterministic row and column counts
- Numeric, categorical, date/time, boolean, identifier, free-text, and empty classifications
- Missing-value and unique-value counts
- Numeric minimum, maximum, mean, median, total, and population standard deviation
- Earliest and latest date/time values
- Constant and empty-column flags
- Candidate KPI, date, and category columns
- Optional one-to-three configuration suggestions from local Ollama
- Repeatable additional source-KPI suggestions after the primary configuration is saved
- Configured KPI names excluded from later Ollama suggestion schemas and profile context
- User-reviewed AI prefilling of KPI direction and shared date, category, and business-objective
  context
- JSON-schema-constrained model responses with a configurable report temperature
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
- Additive source-KPI flow that retains the current primary and all existing KPIs
- Duplicate derived-KPI names rejected instead of silently replacing an existing KPI
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
- Optional user-provided question or purpose stored with each manual visualization
- Deterministic evidence for saved manual charts, including displayed extrema, period change,
  descriptive distribution statistics, or Pearson association as applicable
- Stable `MVE-...` manual-visualization evidence IDs and bounded supporting tables
- Stable dataset-context tokens for configured KPIs, numeric columns, categories, booleans, dates,
  saved visualizations, and evidence
- Dataset-context panels beside the formula editor and business-objective input, with safe insert
  buttons
- Report title, objective, audience, tone, detail level, and user-labelled notes
- Selection of one or more configured KPIs, ranked evidence records, and report-included manual
  charts
- KPI-to-evidence checkbox synchronization: selecting a KPI selects its evidence, while
  deselecting it clears and disables those records
- KPI-visualization dependency synchronization: selecting a KPI chart selects its required KPI,
  while deselecting that KPI removes incompatible chart selections
- Validation that selected evidence belongs to a selected KPI and selected charts remain
  reproducible
- Canonical fingerprints for the business configuration, selected evidence, and chart definitions
- Atomic report-configuration JSON under `instance/report_configurations/`
- Bounded report-generation packages under `instance/report_packages/`
- Exact deterministic observation payloads retained in evidence schema version 2
- Report packages containing selected KPI definitions, source metadata, deterministic evidence,
  and manual-visualization evidence without raw dataset rows or row identifiers
- User notes explicitly labelled as user-provided rather than deterministic evidence
- Stable report-configuration review and edit pages
- Read-only JSON endpoint for reviewing the exact future model input
- Explicit recovery instructions when regenerated evidence or edited charts make a saved report
  selection stale
- Stable `STY-...` story packs containing up to three related evidence records for one metric
- Structured headline, observation, interpretation, follow-up, and caveat fields
- Deterministic fallback stories when an individual model response fails validation
- Report-wide executive summary assembled from the highest-priority included stories
- User-controlled story inclusion and ordering without another Ollama call
- Single-story regeneration without changing the remaining report
- Presentation changes and story regeneration saved as new immutable report versions
- Automatic and manual charts embedded directly in the generated HTML report
- Print CSS and downloadable PDF with the same included stories, claims, charts, sources, and
  optional evidence appendix
- Optional company name and report-author branding
- A bounded catalogue of exact Python-verified display values, qualitative labels, and allowed
  fact-reference paths in model prompts
- Structured responses restricted to the exact supplied `STY-...`, `EVD-...`, and `MVE-...` scope
- Exactly five prioritized executive-summary points generated from validated report stories, with
  metric names, story provenance, and Python-verified numerical references
- Per-story rejection of rounded, invented, or unreferenced quantitative values; unsupported
  percentage or currency symbols; spelled-out quantitative claims; unknown references; modified
  story scope; or causal language
- Ollama-selected fact references resolved into exact numerical claims by Python
- High, medium, or low confidence shown as deterministic evidence strength, with a plain-language
  explanation that it is not a prediction probability
- Python-rendered facts kept separate from explicitly labelled AI-written interpretation
- Calculation, source columns, fact path, and evidence ID shown for each resolved numerical claim
- KPI-only report generation without an Ollama call when no evidence was selected
- Immutable versioned report JSON under `instance/generated_reports/`
- Escaped HTML reports with KPI overview, evidence sections, limitations, source traceability,
  and an optional evidence appendix
- Regeneration that appends a version and preserves the last valid report on Ollama failure
- Current-package fingerprint validation whenever a generated report is reopened
- Complete immutable report history grouped by dataset and report-generation run
- Version-specific report chart assets under `instance/generated_report_assets/`
- Exact historical-version HTML, JSON, and PDF routes with read-only rendering
- Current-versus-historical package status shown for every saved report version
- KPI-only report configuration when no optional evidence or manual chart is selected
- No Ollama call during report configuration
- Localhost-only and debug-off defaults

Ollama is optional for upload, profiling, manual configuration, formulas, deterministic insights,
automatic evidence, manual charts, and KPI-only reports. AI-written report commentary requires the
configured local model.

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

Open `http://127.0.0.1:5000/` to see existing workspaces. Create a workspace
with a name and optional description, open it, and select its one CSV, flat
JSON, or XLSX source. The compatible upload-first form remains at `/upload`.
After source selection, review the deterministic profile. For a workbook with
multiple visible worksheets, choose one worksheet before profiling.
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
settings. The optional question field records what the user wants the chart to answer. Previewed
charts are not retained until explicitly saved. A saved supplementary chart may be included in the
final report, but remains labelled as supplementary rather than KPI evidence.
Select **Configure report content** to choose the KPIs, deterministic evidence, and report-included
manual charts that should be handed to later report generation. The saved review page displays the
source metadata and artifact fingerprints that bind those selections to their current definitions.
It also displays the Python-generated evidence for selected manual charts and links to the exact
bounded JSON package prepared for Milestone 5B.1.
Select **Generate report with llama3.2:latest** to create an immutable HTML report version.
Related evidence is grouped into concise report stories. Python independently resolves every
displayed numerical claim, while Ollama interpretation is separately labelled. Regenerating creates
a new version and does not replace the previous valid report.
On the generated report page, use **Report publishing controls** to include, exclude, or reorder
stories. Saving those choices appends a version without calling Ollama. Each story also has a
targeted regeneration button. Use **Download print-ready PDF** to export the current published
selection with its embedded charts and evidence appendix.

After the first configuration is saved, return to the dataset profile and select
**Suggest additional source KPIs** to request another set from Ollama. Already configured KPI names
are excluded. Reviewing an option prefills its KPI direction plus the shared date, category, and
business-objective context; nothing is saved until the user confirms it. The date, categories, and
business objective are currently dataset-wide settings, so changing them applies to every
configured KPI.

Report configuration and package creation do not call Ollama. Milestone 5B.1 calls Ollama only after
the user explicitly selects **Generate report**. User notes remain explicitly labelled as
user-provided context. If an older evidence artifact is present,
regenerate deterministic insights once before saving a report so the exact observation fields are
available to the package.

Successful state-changing forms use POST/Redirect/GET. Dataset profiles,
suggestion results, formula previews, saved configurations, and insight
reports therefore finish on stable GET URLs, so browser Back, Forward, and
Reload do not repeat a form submission. Rejected lifecycle requests return a
safe 4xx response without changing durable state. Small UI-only state is stored
outside the static directory for 24 hours; raw dataset rows are never placed in
state URLs.

The health endpoint is available at `http://127.0.0.1:5000/health`.

```text
workspace name and optional description
                                  -> persistent empty workspace
                                  -> select one CSV / flat JSON / XLSX source
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
                                  -> validated report-content selection
                                  -> fingerprinted report-configuration JSON
                                  -> deterministic manual-chart evidence
                                  -> bounded report-generation JSON package
                                  -> bounded multi-evidence story packs
                                  -> structured Ollama synthesis
                                  -> Python validation and fact/story separation
                                  -> immutable generated-report JSON and HTML
                                  -> exact HTML / JSON / PDF history
```

Confirm the expected routes with:

```bash
python -m flask --app insight_reporter:create_app routes
```

Expected application routes:

```text
GET   /
GET   /workspaces
GET   /workspaces/<dataset_id>
GET   /workspaces/<dataset_id>/source
GET   /upload
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
GET   /reports/<dataset_id>/configure
GET   /reports/<dataset_id>/configuration
GET   /reports/<dataset_id>/package
GET   /reports/<dataset_id>/generated
GET   /reports/<dataset_id>/generated/<report_id>
GET   /reports/<dataset_id>/generated/<report_id>/json
GET   /reports/<dataset_id>/generated/<report_id>/pdf
GET   /reports/<dataset_id>/generated/<report_id>/versions/<version>
GET   /reports/<dataset_id>/generated/<report_id>/versions/<version>/json
GET   /reports/<dataset_id>/generated/<report_id>/versions/<version>/pdf
GET   /reports/<dataset_id>/generated/<report_id>/versions/<version>/charts/<evidence_id>
GET   /reports/<dataset_id>/history
GET   /health
POST  /workspaces
POST  /workspaces/<dataset_id>/name
POST  /workspaces/<dataset_id>/archive
POST  /workspaces/<dataset_id>/restore
POST  /workspaces/<dataset_id>/source
POST  /workspaces/<dataset_id>/source/name
POST  /workspaces/<dataset_id>/source/archive
POST  /workspaces/<dataset_id>/source/restore
POST  /workspaces/<dataset_id>/reports/<report_id>/name
POST  /workspaces/<dataset_id>/reports/<report_id>/archive
POST  /workspaces/<dataset_id>/reports/<report_id>/restore
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
POST  /reports/<dataset_id>/configure
POST  /reports/<dataset_id>/generate
POST  /reports/<dataset_id>/generated/<report_id>/presentation
POST  /reports/<dataset_id>/generated/<report_id>/stories/<story_id>/regenerate
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
  The combined score is `0.5 × impact + 0.2 × confidence + 0.3 × relevance`; rank ties use the
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
- The optional visualization question is stored as user-provided context, escaped when displayed,
  and limited to 500 characters.
- Report readiness derives chart observations in Python from the saved supporting data. It never
  asks Ollama to interpret a manual chart.
- Manual-chart evidence is descriptive only: category and distribution facts do not imply causes,
  while scatter correlation is explicitly labelled as association rather than causation.
- Drafts use unguessable preview tokens and expire after 24 hours. Saved charts receive stable
  `VIS-...` identifiers.
- Manual visualization generation never calls Ollama.

## Report configuration and readiness rules

Milestone 5A.1 creates the reproducible selection and model-input contract consumed by Milestone
5B.

- At least one configured KPI must be selected. Up to five configured KPIs are supported.
- Evidence is optional. KPI evidence must belong to a selected KPI; dataset-wide evidence such as
  missing-data warnings may be selected independently.
- In the report form, selecting a KPI initially selects all of its deterministic evidence.
  Individual evidence records may then be removed; deselecting the KPI clears all of them.
- Manual charts are optional and must already be saved with **Include in report** enabled.
- Charts that use configured KPI measures must use KPIs selected for the report.
- Each manual chart displays its required report KPIs. Selecting the chart selects those KPIs;
  deselecting a required KPI clears the incompatible chart.
- Supplementary charts remain explicitly labelled and cannot become KPI evidence.
- Title, objective, audience, tone, detail level, and user notes are validated and escaped when
  displayed.
- The current business configuration, selected evidence artifact, and each selected visualization
  definition receive canonical SHA-256 fingerprints.
- Source filename, format, hash, and selected Excel worksheet metadata remain attached.
- Saved report configurations are fully revalidated when opened. Changed source artifacts are
  rejected as stale instead of silently producing a different report.
- A stale report is recovered by returning to report configuration, regenerating deterministic
  insights/evidence when those changed, reopening or regenerating changed manual visualizations,
  and then reviewing and saving the selection again.
- Every selected deterministic evidence record contributes its exact Python observation object.
  Evidence created before schema version 2 must be regenerated rather than inferred from a chart or
  supporting table.
- Every selected manual visualization receives stable deterministic evidence derived from its
  validated chart specification and supporting data.
- The package is bounded to 50 evidence records, 12 supporting rows per deterministic evidence
  record, and 20 manual visualizations. Any omitted selected IDs are listed explicitly.
- Raw dataset rows, internal row numbers, identifier columns, and free-text
  source values are not included in the report-generation package. Values from
  configured category columns may enter only through bounded deterministic
  evidence, allowing reports to name verified products, regions, or segments
  without exposing arbitrary rows.
- The package declares that all numbers come from Python, causal claims are prohibited, and unknown
  evidence or visualization IDs are prohibited.
- `GET /reports/<dataset_id>/package` exposes the exact current package for review after the saved
  configuration and all fingerprints pass validation.
- Saving is atomic, uses the server-generated dataset ID, and stores JSON outside Flask's static
  directory.

## Evidence-grounded narration rules

- The model never receives raw source rows or deterministic supporting tables.
- Up to 10 high-priority evidence records are grouped by metric into at most five stable story
  packs. Each story pack contains at most three related evidence records, with capacity reserved
  for up to two selected manual visualizations.
- Qualitative facts omit numeric leaves, while each evidence descriptor exposes at most five
  Python-calculated fact references with an exact display value and label.
- The model receives qualitative labels, calculation descriptions, limitations, report objective,
  audience, tone, detail level, and path-labelled verified context such as a
  quarter or region. All supplied text remains untrusted data.
- Each story separately records bounded `business_context` descriptors derived
  by Python from deterministic evidence. HTML, JSON, and PDF show these names,
  and a generated management finding is rejected when it ignores available
  business context.
- The JSON schema requires one headline, finding, interpretation, follow-up, caveat, and bounded
  fact-reference list for the exact story ID.
- A story may select up to six facts across its related evidence records. The prompt asks Ollama to
  quote the most useful verified values naturally in its finding and interpretation. Python checks
  every digit-based value and rejects rounding, calculations, invented values, unverified dates,
  evidence IDs, unsupported units, and causal language. Ordinary phrases such as “one area to review” are allowed,
  while spelled-out quantitative claims remain blocked. Percentage signs are allowed only for
  percentage-labelled facts; correlations remain associations.
- Report narration uses a moderately creative default temperature of `0.35`. It is configurable
  without changing Python calculations or the exact values available to the model.
- Story findings are prompted to name the metric and state the observed direction, relationship, or
  comparison directly; interpretation and suggested action remain separate fields.
- Each executive-summary point has a management finding, business
  implication, and recommended action. It must select and quote at least one
  exact Python fact, name its metric, and use only supplied context labels.
- Story confidence is conservatively derived from the linked evidence confidence. High, medium,
  and low describe record support under deterministic rules; they are not model certainty scores
  or prediction probabilities.
- Python verifies that every selected fact belongs to the story pack, resolves the original value
  independently, and displays the claim beside its evidence ID.
- Python copies the original observation object into the generated report and renders it separately
  in the collapsible evidence appendix. Ollama cannot edit an observation or resolved fact value.
- Each resolved claim exposes its evidence ID, fact path, exact value, calculation description, and
  source columns so the appendix explains how the number was produced.
- With no selected evidence, a KPI-only report is generated without calling Ollama.
- An invalid model story is replaced by a deterministic summary. Other stories and every Python
  fact remain in the generated report.
- Generation diagnostics state how many story packs passed AI validation, how
  many used deterministic fallback wording, whether the executive summary was
  AI-generated or assembled deterministically, and which safeguards were
  active.
- Connection or persistence failures occur before saving a new version and never delete or
  overwrite an existing valid report.
- Generated reports are immutable versioned artifacts and reopen only while their source-package
  fingerprint matches the current configuration and evidence.
- Story presentation revisions retain all stories in the JSON artifact, marking excluded stories
  explicitly so they can be restored later.
- HTML and PDF use the same included story ordering and exact fact references. Missing chart files
  are omitted rather than producing broken images.
- HTML values are escaped, user notes remain labelled as user-provided, and model wording remains
  labelled as AI-written interpretation.

## Deterministic calculation rules

- Existing source-column KPIs use sum aggregation for period and segment calculations. Confirmed
  derived KPIs use their validated row-result aggregation or explicit aggregate formula.
- Every configured KPI is analyzed independently and every insight includes its stable metric ID.
- Calendar years are used for at least 24 distinct months, calendar quarters
  for 6–23 months, calendar months for 2–5 months, and calendar days otherwise.
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
- When a target and category are configured, target breaches are also grouped
  by segment so the report can name the best and worst target-attainment
  segments and quantify their average gap and breach percentage.
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
| `APP_OLLAMA_MODEL` | `llama3.2:latest` | Local model used for suggestions and report commentary |
| `APP_OLLAMA_TIMEOUT_SECONDS` | `120` | Local Ollama request timeout |
| `APP_OLLAMA_REPORT_TEMPERATURE` | `0.35` | Report-writing creativity from `0.0` to `1.0`; calculations remain Python-controlled |

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
- Report selections are checked against current KPI, evidence, visualization, and source metadata
  before saving and whenever reopened.
- Report configuration files are stored outside the static directory and use only server-generated
  dataset IDs as filenames.
- Report-generation packages are stored outside the static directory and contain bounded evidence
  instead of raw source rows.
- Generated reports are stored outside the static directory using server-generated dataset and
  report IDs.
- Column names are treated as untrusted data in the remaining configuration-suggestion prompts,
  and model output is treated as untrusted.
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
python scripts/check_ollama.py
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
- Configuration-suggestion confidence is advisory and not a calibrated probability. Generated
  report confidence is instead deterministic evidence strength based on record support.
- Suggestions depend on the semantic ability of the selected local model and require human review.
- No DOCX export yet
- Generated synthesis remains intentionally bounded to related evidence packs rather than
  unconstrained long-form model prose
- A report configuration currently belongs to one dataset scope
- Workspace history is local filesystem state; there is no authentication,
  multi-user isolation, cloud synchronization, or automatic backup. Back up
  `instance/` to retain uploaded sources and report history across disk loss
