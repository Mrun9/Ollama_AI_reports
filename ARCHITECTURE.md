# Project Architecture

This application turns one local dataset into a reviewed report. Python owns
all validation, calculations, evidence, and numerical claims. Ollama is used
only for optional suggestions and narrative wording.

## Start here

Read these files in this order:

1. `src/insight_reporter/app.py` creates and configures the Flask app.
2. `src/insight_reporter/routes.py` shows the browser workflow and delegates
   work to the domain modules.
3. `src/insight_reporter/dataset_view.py` is the common CSV, JSON, and XLSX
   data-access boundary.
4. `src/insight_reporter/insight_engine.py` calculates deterministic findings.
5. `src/insight_reporter/report_narration.py` gives verified evidence to
   Ollama and validates every returned story.

Application code lives under `src/insight_reporter/`. The separate
`scripts/check_ollama.py` command only verifies local model connectivity; it
does not start the web application.

## End-to-end flow

```text
open workspace index
  -> create or reopen a workspace
  -> select one source when the workspace is empty
  -> detect and validate CSV / JSON / XLSX
  -> attach source metadata to the existing workspace identity
  -> build a typed dataset profile
  -> configure source or derived KPIs
  -> calculate deterministic insights
  -> turn insights into ranked evidence and charts
  -> optionally create manual visualizations
  -> select report content
  -> build a bounded, fingerprinted report package
  -> ask Ollama for structured stories
  -> validate model text against Python facts
  -> save versioned HTML/JSON and export PDF
  -> reopen the workspace or any immutable report version later
```

The browser never passes raw rows directly to Ollama. The report package
contains bounded evidence descriptors and Python-calculated fact references.

## Module map

### Application and HTTP

| Module | Responsibility |
| --- | --- |
| `app.py` | Flask factory, runtime directories, logging, security headers |
| `config.py` | Environment-backed limits and Ollama settings |
| `routes.py` | HTTP request orchestration and template rendering |
| `navigation_state.py` | Short-lived POST/Redirect/GET UI state |
| `workspace_history.py` | Durable workspace identity, progress reconstruction, and local history |

`routes.py` is grouped into labelled sections for dataset and KPI setup,
reports, visualizations, insights, and shared helpers. Route functions should
remain thin: load validated inputs, call a domain module, and render or
redirect.

### Dataset and KPI domain

| Module | Responsibility |
| --- | --- |
| `dataset_ingestion.py` | Safely retains an uploaded CSV, JSON, or XLSX file |
| `dataset_view.py` | Normalizes supported files behind one row/column interface |
| `dataset_profile.py` | Infers types and calculates column statistics |
| `business_config.py` | Stores the reviewed KPI registry and shared dimensions |
| `formula_engine.py` | Parses and evaluates the restricted formula language |
| `derived_metrics.py` | Validates and previews derived KPI definitions |
| `configuration_suggestions.py` | Optional Ollama suggestions for source KPIs |
| `derived_kpi_suggestions.py` | Optional Ollama suggestions for derived KPIs |

### Analysis and visualization

| Module | Responsibility |
| --- | --- |
| `insight_engine.py` | Python-only period changes, segment target performance, anomalies, correlations, and benchmarks |
| `evidence_layer.py` | Prioritizes management-relevant insights and converts them into evidence records and charts |
| `visualization_builder.py` | Validates, calculates, renders, and saves manual charts |
| `manual_visualization_evidence.py` | Produces deterministic evidence for manual charts |
| `dataset_context.py` | Builds safe field tokens shown beside configuration forms |

### Report domain

| Module | Responsibility |
| --- | --- |
| `report_configuration.py` | Validates the user's selected KPIs, evidence, and charts |
| `report_generation_package.py` | Builds the exact bounded input contract for narration |
| `report_narration.py` | Validates grounded stories, verified product/region/period context, structured management summaries, AI diagnostics, and versioned report JSON |
| `report_pdf.py` | Renders the validated report artifact as a PDF |

## Runtime artifacts

Source code is under `src/`. Local workspace state is under `instance/`.
Milestone 6A makes that directory the persistent project store: do not delete
it if uploaded sources or report history must be retained, and include it in
local backups when recovery matters.

| Directory | Contents |
| --- | --- |
| `instance/uploads/` | Randomly named retained source files |
| `instance/workspaces/` | Safe human-readable identity for each retained dataset |
| `instance/configurations/` | Reviewed KPI configurations |
| `instance/insights/` | Deterministic Python insight reports |
| `instance/evidence/` | Ranked, traceable evidence records |
| `instance/charts/` | Automatic evidence chart images |
| `instance/visualizations/` | Saved manual visualization definitions |
| `instance/visualization_previews/` | Short-lived chart previews |
| `instance/report_configurations/` | User-selected report content |
| `instance/report_packages/` | Bounded narration input packages |
| `instance/generated_reports/` | Immutable versioned report JSON |
| `instance/generated_report_assets/` | Version-specific chart snapshots used by historical HTML/PDF |
| `instance/navigation_state/` | Short-lived form and validation state |
| `instance/trash/sources/<dataset_id>/` | Recoverably deleted source and XLSX selection sidecar |

In the workspace-first flow, the dataset ID is allocated when the empty
workspace is created and becomes the safe filename stem when its source is
attached. The compatible `/upload` flow still allocates the ID during upload.
Most artifacts use that same ID, which is how the workflow joins its files
without a database.

The workspace index does not duplicate downstream state. It scans the
workspace metadata plus dataset-bound artifacts to derive the latest completed
stage, last activity, and active/archived report counts. This makes empty
workspaces visible even before an upload. Pre-6A uploads remain discoverable
through a safe fallback identity and are adopted into current metadata on the
first lifecycle edit.

Workspace metadata schema 2 stores mutable presentation and lifecycle state:
name, description, optional source metadata, archive timestamps, report
aliases, and archived report IDs. Workspace/report deletion changes this
metadata only. Source deletion is the exception: the safe source file and XLSX
selection sidecar are moved transactionally to recoverable trash. Reports and
their chart snapshots are never moved or rewritten.

Generated-report filenames contain both a monotonically increasing dataset
version and a report ID. The ordinary report route opens the latest revision
for a report ID and requires the current package fingerprint. History routes
load an exact filename and intentionally render it read-only, allowing older
snapshots to remain inspectable after upstream configuration changes.
Each newly saved version also receives its own chart-asset directory. Saving
is rolled back if that snapshot cannot be created, preventing a report-history
entry whose visuals were never retained.

## Trust boundaries

There are three different kinds of content:

1. **Python facts** — calculations and exact numeric values. These are trusted
   only after deterministic validation.
2. **User context** — objectives, titles, notes, and chart questions. These are
   retained as user-provided text and escaped when rendered.
3. **Ollama text** — optional suggestions and report prose. Structured model
   responses are treated as untrusted and validated before saving.

If an Ollama story invents or changes a number, references unknown evidence,
duplicates a fact, or makes a prohibited causal claim, it is retried and may
ultimately be replaced by a deterministic summary. A report with evidence but
zero accepted AI stories is not saved. Executive-summary points are separately
validated against their cited stories and facts; invalid summaries are retried
before a clearly labelled story-based fallback is used.

## Where to make common changes

- Add a supported upload format: `dataset_view.py`, then
  `dataset_ingestion.py`.
- Add a deterministic analysis: `insight_engine.py`, then map its evidence and
  chart behavior in `evidence_layer.py`.
- Add a manual chart type: `visualization_builder.py`.
- Change the report-selection form: `report_configuration.py`, its templates,
  and the corresponding route.
- Change what Ollama receives: `report_generation_package.py` or the bounded
  descriptor construction in `report_narration.py`.
- Change PDF layout: `report_pdf.py`.
- Change workspace stages or history metadata: `workspace_history.py`.
- Change workspace/source/report lifecycle routes: the workspace section of
  `routes.py`, then `workspace.html` and `workspaces.html`.
- Change environment defaults: `config.py` and `.env.example`.

## Verification

```bash
conda activate ollama-env
pytest -q
ruff check .
```

Tests mirror the domain module names. Route tests cover the full browser
workflow using isolated temporary artifact directories.
