# Model Run Metrics

The application records one append-only CSV row for every local Ollama
`chat()` request made through the browser workflow. This provides a stable
baseline for comparing prompt revisions, models, retry rates, token use, and
latency without storing prompt or response text.

## Location and lifecycle

The default file is:

```text
instance/model_run_metrics/model_runs.csv
```

`create_app()` creates the directory. The CSV itself is created lazily by the
first model request. It belongs to the filesystem workspace store; no database
is involved.

Every model request is logged, including:

- the command-line Ollama connectivity check;
- configuration suggestions;
- derived-KPI suggestions;
- each report-story attempt;
- single-story regeneration attempts; and
- each five-point executive-summary attempt.

A retry is a new row because it consumes additional time and tokens.
`workflow_run_id` groups all rows initiated by one report-generation or
single-story-regeneration operation. `run_id` uniquely identifies one actual
Ollama request.

The writer uses a process/thread-safe append lock, writes the header once, and
flushes each row. A metrics-write failure is logged and does not make the
model task fail.

## Column dictionary

| Column | Meaning |
| --- | --- |
| `schema_version` | CSV contract version; currently `1` |
| `run_id` | Unique ID for one Ollama request |
| `workflow_run_id` | ID shared by requests from one higher-level operation |
| `started_at_utc` | Request start time in UTC |
| `task_type` | `ollama_connectivity_check`, `configuration_suggestions`, `derived_kpi_suggestions`, `report_story`, `report_story_regeneration`, or `executive_summary` |
| `prompt_version` | Explicit version label for the prompt contract |
| `dataset_id` | Workspace/dataset identity when available |
| `report_id` | Existing report identity for story regeneration; blank during initial report generation because the immutable report ID is allocated afterward |
| `story_id` | Stable story-pack identity for story tasks |
| `attempt` | One-based attempt number |
| `model` | Configured Ollama model |
| `status` | `validated`, `validation_rejected`, or `request_failed` |
| `wall_time_ms` | Wall-clock time around `client.chat()` only |
| `prompt_tokens` | Ollama `prompt_eval_count`, when returned |
| `completion_tokens` | Ollama `eval_count`, when returned |
| `total_tokens` | Prompt plus completion tokens when both official counts exist |
| `completion_tokens_per_second` | Completion count divided by Ollama evaluation duration |
| `ollama_total_duration_ms` | Ollama-reported total model duration |
| `ollama_load_duration_ms` | Time Ollama spent loading the model |
| `ollama_prompt_eval_duration_ms` | Ollama prompt-evaluation duration |
| `ollama_eval_duration_ms` | Ollama generation duration |
| `message_count` | Number of messages sent, including retry-repair messages |
| `prompt_characters` | Total message-content characters; useful when token counts are unavailable |
| `response_characters` | Returned message length, never the response itself |
| `temperature` | Temperature sent for this attempt |
| `num_ctx` | Configured context window when explicitly supplied |
| `num_predict` | Configured output-token ceiling when explicitly supplied |
| `error_type` | Exception class for failed requests or rejected responses; error text is not retained |

Ollama token/duration cells remain blank when the installed client/server does
not return those official fields. The application does not estimate tokens,
because estimated and model-tokenizer counts would make comparisons
misleading.

## Outcome semantics

- `validated`: Ollama returned a response and the module's normal Python
  schema/grounding validation accepted it.
- `validation_rejected`: Ollama returned a response, but its structured output
  failed validation. A later retry may still succeed.
- `request_failed`: no response was received, such as when Ollama is offline
  or the request times out.

Metrics are recorded even when the overall report is not saved. This is
intentional: failed and rejected experiments are part of prompt performance.

## Comparing prompt revisions

Whenever a prompt or its output contract changes materially, increment its
constant in the owning module:

- `_PROMPT_VERSION` in `configuration_suggestions.py`;
- `_PROMPT_VERSION` in `derived_kpi_suggestions.py`;
- `_STORY_PROMPT_VERSION` in `report_narration.py`; or
- `_SUMMARY_PROMPT_VERSION` in `report_narration.py`.
- `PROMPT_VERSION` in `scripts/check_ollama.py`.

For a fair comparison, hold the dataset, model, hardware, and Ollama settings
constant. Group rows by `task_type`, `prompt_version`, and `model`, then
compare:

1. validation rate: validated rows divided by all rows;
2. first-attempt success: validated rows where `attempt = 1`;
3. median `wall_time_ms`, not only the average;
4. median `total_tokens` and `completion_tokens`; and
5. retries per `workflow_run_id`.

Separate cold starts from warm runs when `ollama_load_duration_ms` is
material. A faster prompt that validates less often can cost more overall
because repair attempts consume another full request.

## Privacy boundary

The CSV stores identifiers, counts, settings, timings, statuses, and exception
class names. It does not store:

- prompt text;
- dataset rows or column values;
- model response text;
- user notes or objectives; or
- exception messages.

The dataset ID and story/report IDs are retained only so runs can be compared
within the same reproducible workflow.
