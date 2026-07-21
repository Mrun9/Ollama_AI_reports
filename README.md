# AI Insight Reporter

AI Insight Reporter is a local-first proof of concept for turning raw business data into
evidence-grounded reports. Python will perform deterministic calculations, and a local Ollama
model will later generate narrative text from verified evidence.

The project currently implements **Milestone 2: Dataset Profiling and Business Configuration**.
It profiles one securely ingested CSV and records validated user selections, but deliberately does
not call Ollama or generate narrative reports yet.

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
- User confirmation of KPI direction, dimensions, target, and business objective
- Validated JSON configuration under `instance/configurations/`
- Localhost-only and debug-off defaults

Milestone 2 does not require Ollama. The root `app.py` remains available as a separate local
Ollama connectivity starter for later milestones.

## Setup with the existing Conda environment

```bash
conda activate ollama-env
cd "/Users/mrunal/Documents/Projects/ollama project"
python -m pip install -r requirements-dev.txt
```

Using Conda is supported; a separate `.venv` is not required.

## Run the CSV upload service

```bash
python -m flask --app insight_reporter:create_app run --host 127.0.0.1 --port 5000
```

Open `http://127.0.0.1:5000/` to upload a CSV, review its deterministic profile, and confirm the
business configuration. The health endpoint is available at `http://127.0.0.1:5000/health`.

Confirm the expected routes with:

```bash
python -m flask --app insight_reporter:create_app routes
```

Expected application routes:

```text
GET   /
GET   /health
POST  /upload
POST  /configure/<dataset_id>
```

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
- The application does not execute uploaded content or use `eval`, `exec`, pickle, or shell commands.
- Flask and Ollama must remain bound to loopback unless company IT approves another design.
- Use only dummy data until company data handling and retention are approved.

## Optional standalone Ollama check

After Ollama is installed, running, and has an approved model downloaded:

```bash
python app.py
```

This standalone check is not part of the Milestone 1 acceptance gate.

## Current limitations

- No JSON upload
- Type inference is heuristic and must be confirmed by the user.
- Empty strings and the markers `NA`, `N/A`, `null`, `none`, and `NaN` count as missing.
- Numeric profiling is descriptive only; no business insights or trend calculations are generated.
- No Ollama narration in the Flask workflow
- No report generation
- No persistent history, authentication, or multi-user isolation
