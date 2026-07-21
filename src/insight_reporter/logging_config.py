"""Application logging with conservative redaction."""

import logging
import re
from typing import Final

from flask import Flask

_SECRET_ASSIGNMENT: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(password|passwd|token|api[_-]?key|secret)\b(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_TOKEN: Final[re.Pattern[str]] = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+\-/]+=*")


def redact_sensitive_text(value: object) -> str:
    """Redact common secret representations from a log value."""

    text = str(value)
    text = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", text)
    return _BEARER_TOKEN.sub("Bearer [REDACTED]", text)


class SensitiveDataFilter(logging.Filter):
    """Sanitize the message and arguments of each application log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Render first so replacing arguments with strings cannot break numeric
        # format placeholders such as ``%d``.
        try:
            rendered_message = record.getMessage()
        except (TypeError, ValueError):
            rendered_message = str(record.msg)

        record.msg = redact_sensitive_text(rendered_message)
        record.args = ()
        return True


def configure_logging(app: Flask) -> None:
    """Apply level and redaction without logging request or uploaded content."""

    app.logger.setLevel(app.config["LOG_LEVEL"])

    if not any(isinstance(item, SensitiveDataFilter) for item in app.logger.filters):
        app.logger.addFilter(SensitiveDataFilter())

    for handler in app.logger.handlers:
        if not any(isinstance(item, SensitiveDataFilter) for item in handler.filters):
            handler.addFilter(SensitiveDataFilter())
