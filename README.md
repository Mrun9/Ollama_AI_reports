# AI Insight Reporter

AI Insight Reporter is a local-first proof of concept for turning raw business data into
evidence-grounded reports. Python will perform deterministic calculations, and a local Ollama
model provides optional configuration suggestions before later generating narrative text from
verified evidence.

The project currently implements **Milestone 2.5: AI-Assisted Configuration**. It profiles one
securely ingested CSV, optionally asks local Ollama for structured configuration suggestions, and
records only a Python-validated, user-confirmed selection. It does not generate insights or reports
yet.

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
- Validated JSON configuration under `instance/configurations/`
- Localhost-only and debug-off defaults

Ollama is optional: CSV upload, profiling, and manual configuration continue to work without it.

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

The health endpoint is available at `http://127.0.0.1:5000/health`.

```text
CSV -> Python validation/profile -> optional Ollama suggestions
                                  -> Python validation
                                  -> user review/edit
                                  -> final BusinessConfiguration
```

Confirm the expected routes with:

```bash
python -m flask --app insight_reporter:create_app routes
```

Expected application routes:

```text
GET   /
GET   /health
POST  /upload
POST  /suggest/<dataset_id>
POST  /review-suggestion/<dataset_id>
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
- Ollama receives column metadata, descriptive statistics, and candidate lists—not raw preview rows.
- Column names are treated as untrusted data in the prompt and model output is treated as untrusted.
- Structured output is validated again in Python before suggestions are displayed.
- The model-facing JSON grammar stays simple for `llama3.2` while restricting KPI,
  date, and category selections to profiler candidates; Python enforces the remaining constraints.
- Ollama suggestions cannot set a target or benchmark; only the user can enter one.
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
- Numeric profiling is descriptive only; no business insights or trend calculations are generated.
- AI confidence is advisory and not a calibrated probability.
- Suggestions depend on the semantic ability of the selected local model.
- No Ollama report narration in the Flask workflow
- No report generation
- No persistent history, authentication, or multi-user isolation
