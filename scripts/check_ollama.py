"""Verify that the configured local Ollama model can answer one request."""

import os
from pathlib import Path

from ollama import Client

from insight_reporter.model_run_metrics import measure_model_run

HOST = "http://127.0.0.1:11434"
MODEL = os.getenv("APP_OLLAMA_MODEL", "llama3.2:latest")
METRICS_DIR = (
    Path(__file__).resolve().parents[1]
    / "instance"
    / "model_run_metrics"
)
PROMPT_VERSION = "ollama_connectivity_check.v1"
PROMPT = os.getenv(
    "OLLAMA_CHECK_PROMPT",
    "Reply with a short confirmation that local model inference is working.",
)


def main() -> None:
    messages = [{"role": "user", "content": PROMPT}]
    try:
        with measure_model_run(
            metrics_dir=METRICS_DIR,
            task_type="ollama_connectivity_check",
            prompt_version=PROMPT_VERSION,
            model=MODEL,
            messages=messages,
        ) as measurement:
            response = Client(host=HOST).chat(
                model=MODEL,
                messages=messages,
            )
            measurement.capture_response(response)
            measurement.mark_validated()
        print(response["message"]["content"])
    except Exception as error:
        raise SystemExit(
            f"Could not use {MODEL} through Ollama at {HOST}: {error}"
        ) from error


if __name__ == "__main__":
    main()
