"""Logging-redaction tests."""

import logging

from insight_reporter.logging_config import SensitiveDataFilter, redact_sensitive_text


def test_redacts_common_secret_assignments() -> None:
    source = "password=hunter2 api_key:abc123 secret = private-value"

    redacted = redact_sensitive_text(source)

    assert "hunter2" not in redacted
    assert "abc123" not in redacted
    assert "private-value" not in redacted
    assert redacted.count("[REDACTED]") == 3


def test_redacts_bearer_tokens() -> None:
    assert redact_sensitive_text("Bearer abc.def-123") == "Bearer [REDACTED]"


def test_filter_redacts_log_arguments() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="credentials: %s",
        args=("token=top-secret",),
        exc_info=None,
    )

    assert SensitiveDataFilter().filter(record) is True
    assert "top-secret" not in record.getMessage()


def test_filter_preserves_numeric_formatting() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="processed %d records",
        args=(5,),
        exc_info=None,
    )

    assert SensitiveDataFilter().filter(record) is True
    assert record.getMessage() == "processed 5 records"
