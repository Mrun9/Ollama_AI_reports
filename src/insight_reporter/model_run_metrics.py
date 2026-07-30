"""Append-only, privacy-safe measurements for local model calls."""

from __future__ import annotations

import csv
import logging
import math
import os
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

try:
    import fcntl
except ImportError:  # pragma: no cover - the application currently targets macOS/Linux.
    fcntl = None  # type: ignore[assignment]

_LOGGER = logging.getLogger(__name__)
_CSV_FILENAME = "model_runs.csv"
_SCHEMA_VERSION = 1
_WRITE_LOCK = threading.Lock()
_FIELDNAMES = (
    "schema_version",
    "run_id",
    "workflow_run_id",
    "started_at_utc",
    "task_type",
    "prompt_version",
    "dataset_id",
    "report_id",
    "story_id",
    "attempt",
    "model",
    "status",
    "wall_time_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "completion_tokens_per_second",
    "ollama_total_duration_ms",
    "ollama_load_duration_ms",
    "ollama_prompt_eval_duration_ms",
    "ollama_eval_duration_ms",
    "message_count",
    "prompt_characters",
    "response_characters",
    "temperature",
    "num_ctx",
    "num_predict",
    "error_type",
)


class ModelRunMeasurement(AbstractContextManager["ModelRunMeasurement"]):
    """Measure one model request and append exactly one CSV row on exit.

    Callers capture the response immediately after ``client.chat`` returns and
    mark it validated only after their normal untrusted-output checks pass.
    CSV failures are logged but never change the model workflow.
    """

    def __init__(
        self,
        *,
        metrics_dir: Path | None,
        task_type: str,
        prompt_version: str,
        model: str,
        messages: Sequence[Mapping[str, object]],
        options: Mapping[str, object] | None = None,
        dataset_id: str = "",
        report_id: str = "",
        story_id: str = "",
        attempt: int = 1,
        workflow_run_id: str = "",
    ) -> None:
        self._metrics_dir = metrics_dir
        self._task_type = task_type
        self._prompt_version = prompt_version
        self._model = model
        self._dataset_id = dataset_id
        self._report_id = report_id
        self._story_id = story_id
        self._attempt = attempt
        self._workflow_run_id = workflow_run_id or uuid.uuid4().hex
        self._message_count = len(messages)
        self._prompt_characters = sum(
            len(content)
            for message in messages
            if isinstance((content := message.get("content")), str)
        )
        self._options = options or {}
        self._started_at = datetime.now(UTC)
        self._started_ns = time.perf_counter_ns()
        self._wall_time_ms: float | None = None
        self._response: object | None = None
        self._validated = False

    def capture_response(self, response: object) -> None:
        """Capture Ollama usage metadata as soon as the request completes."""

        self._wall_time_ms = _elapsed_ms(self._started_ns)
        self._response = response

    def mark_validated(self) -> None:
        """Record that the caller accepted the structured model response."""

        self._validated = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._wall_time_ms is None:
            self._wall_time_ms = _elapsed_ms(self._started_ns)
        status = (
            "validated"
            if self._validated
            else "validation_rejected"
            if self._response is not None
            else "request_failed"
        )
        row = self._row(
            status=status,
            error_type=exc_type.__name__ if exc_type is not None else "",
        )
        if self._metrics_dir is not None:
            _append_row(self._metrics_dir, row)
        return None

    def _row(self, *, status: str, error_type: str) -> dict[str, object]:
        prompt_tokens = _optional_nonnegative_int(
            _response_value(self._response, "prompt_eval_count")
        )
        completion_tokens = _optional_nonnegative_int(
            _response_value(self._response, "eval_count")
        )
        total_tokens = (
            prompt_tokens + completion_tokens
            if prompt_tokens is not None and completion_tokens is not None
            else None
        )
        eval_duration_ns = _optional_nonnegative_number(
            _response_value(self._response, "eval_duration")
        )
        tokens_per_second = (
            completion_tokens / (eval_duration_ns / 1_000_000_000)
            if completion_tokens is not None
            and eval_duration_ns is not None
            and eval_duration_ns > 0
            else None
        )
        return {
            "schema_version": _SCHEMA_VERSION,
            "run_id": uuid.uuid4().hex,
            "workflow_run_id": self._workflow_run_id,
            "started_at_utc": self._started_at.isoformat(),
            "task_type": self._task_type,
            "prompt_version": self._prompt_version,
            "dataset_id": self._dataset_id,
            "report_id": self._report_id,
            "story_id": self._story_id,
            "attempt": self._attempt,
            "model": self._model,
            "status": status,
            "wall_time_ms": _rounded(self._wall_time_ms),
            "prompt_tokens": _blank_if_none(prompt_tokens),
            "completion_tokens": _blank_if_none(completion_tokens),
            "total_tokens": _blank_if_none(total_tokens),
            "completion_tokens_per_second": _blank_if_none(
                _rounded(tokens_per_second)
            ),
            "ollama_total_duration_ms": _duration_ms(
                self._response, "total_duration"
            ),
            "ollama_load_duration_ms": _duration_ms(
                self._response, "load_duration"
            ),
            "ollama_prompt_eval_duration_ms": _duration_ms(
                self._response, "prompt_eval_duration"
            ),
            "ollama_eval_duration_ms": _duration_ms(
                self._response, "eval_duration"
            ),
            "message_count": self._message_count,
            "prompt_characters": self._prompt_characters,
            "response_characters": _response_characters(self._response),
            "temperature": _option_value(self._options, "temperature"),
            "num_ctx": _option_value(self._options, "num_ctx"),
            "num_predict": _option_value(self._options, "num_predict"),
            "error_type": error_type,
        }


def measure_model_run(
    *,
    metrics_dir: Path | None,
    task_type: str,
    prompt_version: str,
    model: str,
    messages: Sequence[Mapping[str, object]],
    options: Mapping[str, object] | None = None,
    dataset_id: str = "",
    report_id: str = "",
    story_id: str = "",
    attempt: int = 1,
    workflow_run_id: str = "",
) -> ModelRunMeasurement:
    """Create a measurement context for one local model request."""

    return ModelRunMeasurement(
        metrics_dir=metrics_dir,
        task_type=task_type,
        prompt_version=prompt_version,
        model=model,
        messages=messages,
        options=options,
        dataset_id=dataset_id,
        report_id=report_id,
        story_id=story_id,
        attempt=attempt,
        workflow_run_id=workflow_run_id,
    )


def model_metrics_csv_path(metrics_dir: Path) -> Path:
    """Return the stable CSV path for documentation, tests, and tooling."""

    return metrics_dir / _CSV_FILENAME


def _append_row(metrics_dir: Path, row: Mapping[str, object]) -> None:
    try:
        metrics_dir.mkdir(parents=True, exist_ok=True)
        path = model_metrics_csv_path(metrics_dir)
        with _WRITE_LOCK, path.open("a+", encoding="utf-8", newline="") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0, os.SEEK_END)
                is_empty = handle.tell() == 0
                writer = csv.DictWriter(handle, fieldnames=_FIELDNAMES)
                if is_empty:
                    writer.writeheader()
                else:
                    handle.seek(0)
                    header = next(csv.reader(handle), ())
                    if tuple(header) != _FIELDNAMES:
                        raise ValueError(
                            "Existing model metrics CSV has an incompatible header."
                        )
                    handle.seek(0, os.SEEK_END)
                writer.writerow(
                    {
                        field: _spreadsheet_safe(row.get(field, ""))
                        for field in _FIELDNAMES
                    }
                )
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (OSError, csv.Error, TypeError, ValueError) as error:
        _LOGGER.warning(
            "Model metrics could not be written: error_type=%s",
            type(error).__name__,
        )


def _response_value(response: object | None, key: str) -> object:
    if response is None:
        return None
    if isinstance(response, Mapping):
        return response.get(key)
    return getattr(response, key, None)


def _response_characters(response: object | None) -> object:
    message = _response_value(response, "message")
    if isinstance(message, Mapping):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    return len(content) if isinstance(content, str) else ""


def _duration_ms(response: object | None, key: str) -> object:
    duration_ns = _optional_nonnegative_number(_response_value(response, key))
    return (
        _blank_if_none(_rounded(duration_ns / 1_000_000))
        if duration_ns is not None
        else ""
    )


def _option_value(options: Mapping[str, object], key: str) -> object:
    value = options.get(key)
    return value if isinstance(value, int | float) and not isinstance(value, bool) else ""


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _optional_nonnegative_number(value: object) -> float | None:
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    ):
        return float(value)
    return None


def _elapsed_ms(started_ns: int) -> float:
    return (time.perf_counter_ns() - started_ns) / 1_000_000


def _rounded(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


def _blank_if_none(value: object | None) -> object:
    return "" if value is None else value


def _spreadsheet_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value
