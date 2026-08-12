"""Grounded Ollama narration for verified data-chat calculations."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from ollama import Client

from insight_reporter.query_insight_engine import QueryInsight


class DatasetChatNarrationError(ValueError):
    """Raised when a safe, grounded narration cannot be produced."""


class _ChatClient(Protocol):
    def chat(self, **kwargs: Any) -> object: ...


_ANSWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}
_SYSTEM_PROMPT = """Rewrite a verified data-analysis result as a concise, natural answer.

Rules:
- Answer the user's exact question directly.
- Use only facts supplied in verified_answer and evidence.
- Never calculate, estimate, infer causes, or introduce new facts.
- Preserve every number, date, column name, unit, and qualification exactly.
- Do not claim causation unless the verified answer explicitly does.
- Use plain text, not Markdown headings, tables, or bullet points.
- Return only the required JSON object: {"answer": "..."}.
"""
_NUMBER = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?")
_MAX_ANSWER_CHARACTERS = 2_000
_MAX_EVIDENCE_ROWS = 12


def narrate_verified_answer(
    *,
    question: str,
    verified_answer: str,
    insights: tuple[QueryInsight, ...],
    model: str,
    host: str,
    timeout_seconds: int,
    client: _ChatClient | None = None,
) -> str:
    """Rewrite verified facts without allowing the model to add numeric claims."""

    if not verified_answer.strip() or not insights:
        raise DatasetChatNarrationError("Verified evidence is required for narration.")
    if client is None:
        client = Client(host=host, timeout=float(timeout_seconds))
    payload = {
        "question": question,
        "verified_answer": verified_answer,
        "evidence": [_evidence_item(insight) for insight in insights[:4]],
    }
    try:
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
            format=_ANSWER_SCHEMA,
            options={"temperature": 0.1, "num_ctx": 4096, "num_predict": 400},
        )
        result = json.loads(_response_content(response))
    except DatasetChatNarrationError:
        raise
    except Exception as error:
        raise DatasetChatNarrationError(
            f"Ollama narration is unavailable ({type(error).__name__})."
        ) from error
    answer = result.get("answer") if isinstance(result, dict) else None
    if not isinstance(answer, str):
        raise DatasetChatNarrationError("Ollama narration did not contain an answer.")
    answer = " ".join(answer.split())
    if not answer or len(answer) > _MAX_ANSWER_CHARACTERS:
        raise DatasetChatNarrationError("Ollama narration had an invalid length.")
    source_text = json.dumps(payload, ensure_ascii=False, default=str)
    introduced = _numeric_values(answer) - _numeric_values(source_text)
    if introduced:
        raise DatasetChatNarrationError(
            "Ollama narration introduced numbers that were not in the verified evidence."
        )
    return answer


def _evidence_item(insight: QueryInsight) -> dict[str, object]:
    return {
        "title": insight.title,
        "finding": insight.finding,
        "columns_used": list(insight.columns_used),
        "calculation": insight.calculation,
        "supporting_data": list(insight.supporting_data[:_MAX_EVIDENCE_ROWS]),
        "limitations": list(insight.limitations),
    }


def _response_content(response: object) -> str:
    if isinstance(response, dict):
        message = response.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
    message = getattr(response, "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    raise DatasetChatNarrationError("Ollama narration returned no readable content.")


def _numeric_values(value: str) -> set[Decimal]:
    numbers: set[Decimal] = set()
    for match in _NUMBER.findall(value):
        try:
            numbers.add(Decimal(match.replace(",", "")).normalize())
        except InvalidOperation:
            continue
    return numbers
