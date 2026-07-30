"""Tests for append-only local model-run measurements."""

import csv
from pathlib import Path

import pytest

from insight_reporter.model_run_metrics import (
    measure_model_run,
    model_metrics_csv_path,
)


def _rows(metrics_dir: Path) -> list[dict[str, str]]:
    with model_metrics_csv_path(metrics_dir).open(
        encoding="utf-8",
        newline="",
    ) as handle:
        return list(csv.DictReader(handle))


def test_validated_run_records_official_ollama_usage_and_prompt_metadata(
    tmp_path: Path,
) -> None:
    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "User prompt."},
    ]
    response = {
        "message": {"content": '{"answer":"ok"}'},
        "prompt_eval_count": 120,
        "eval_count": 30,
        "total_duration": 2_500_000_000,
        "load_duration": 200_000_000,
        "prompt_eval_duration": 800_000_000,
        "eval_duration": 1_500_000_000,
    }

    with measure_model_run(
        metrics_dir=tmp_path,
        task_type="executive_summary",
        prompt_version="executive_summary.v2",
        model="test-model",
        messages=messages,
        options={"temperature": 0.2, "num_ctx": 4096, "num_predict": 500},
        dataset_id="a" * 32,
        story_id="STY-0123456789ABCDEF",
        attempt=2,
        workflow_run_id="workflow-123",
    ) as measurement:
        measurement.capture_response(response)
        measurement.mark_validated()

    [row] = _rows(tmp_path)
    assert row["status"] == "validated"
    assert row["workflow_run_id"] == "workflow-123"
    assert row["task_type"] == "executive_summary"
    assert row["prompt_version"] == "executive_summary.v2"
    assert row["dataset_id"] == "a" * 32
    assert row["attempt"] == "2"
    assert row["prompt_tokens"] == "120"
    assert row["completion_tokens"] == "30"
    assert row["total_tokens"] == "150"
    assert row["completion_tokens_per_second"] == "20.0"
    assert row["ollama_total_duration_ms"] == "2500.0"
    assert row["ollama_eval_duration_ms"] == "1500.0"
    assert row["message_count"] == "2"
    assert row["prompt_characters"] == str(
        len("System prompt.") + len("User prompt.")
    )
    assert row["response_characters"] == str(len('{"answer":"ok"}'))
    assert float(row["wall_time_ms"]) >= 0
    assert row["error_type"] == ""


def test_rejected_response_and_failed_request_are_separate_rows(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="invalid"):
        with measure_model_run(
            metrics_dir=tmp_path,
            task_type="report_story",
            prompt_version="report_story.v1",
            model="test-model",
            messages=[],
        ) as rejected:
            rejected.capture_response({"message": {"content": "{}"}})
            raise ValueError("invalid")

    with pytest.raises(ConnectionError, match="offline"):
        with measure_model_run(
            metrics_dir=tmp_path,
            task_type="configuration_suggestions",
            prompt_version="configuration_suggestions.v1",
            model="test-model",
            messages=[],
        ):
            raise ConnectionError("offline")

    rows = _rows(tmp_path)
    assert [row["status"] for row in rows] == [
        "validation_rejected",
        "request_failed",
    ]
    assert [row["error_type"] for row in rows] == [
        "ValueError",
        "ConnectionError",
    ]
    assert rows[0]["prompt_tokens"] == ""
    assert rows[1]["completion_tokens"] == ""


def test_disabled_measurement_does_not_create_a_csv(tmp_path: Path) -> None:
    with measure_model_run(
        metrics_dir=None,
        task_type="test",
        prompt_version="test.v1",
        model="test-model",
        messages=[],
    ) as measurement:
        measurement.capture_response({})
        measurement.mark_validated()

    assert not model_metrics_csv_path(tmp_path).exists()
