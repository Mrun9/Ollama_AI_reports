"""Durable, source-bound evidence captured from verified data-chat answers."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from insight_reporter.query_insight_engine import QueryInsight

_DATASET_ID = re.compile(r"[0-9a-f]{32}")
_EVIDENCE_ID = re.compile(r"EVD-[0-9A-F]{16}")
_MAX_SAVED_QUESTIONS = 100
_MAX_ARTIFACT_BYTES = 250_000


class SavedChatEvidenceError(ValueError):
    """Raised when saved chat evidence is invalid or cannot be retained."""


@dataclass(frozen=True)
class SavedChatEvidence:
    schema_version: int
    dataset_id: str
    evidence_id: str
    source_sha256: str
    source: dict[str, object]
    question: str
    verified_answer: str
    displayed_answer: str
    insights: tuple[dict[str, object], ...]
    saved_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "evidence_id": self.evidence_id,
            "source_sha256": self.source_sha256,
            "source": self.source,
            "question": self.question,
            "verified_answer": self.verified_answer,
            "displayed_answer": self.displayed_answer,
            "insights": list(self.insights),
            "saved_at": self.saved_at,
        }

    def evidence_record(self, *, rank: int) -> dict[str, object]:
        """Expose this Q&A using the existing deterministic evidence contract."""

        columns = tuple(
            dict.fromkeys(
                column
                for insight in self.insights
                for column in _string_list(insight.get("columns_used"))
            )
        )
        limitations = tuple(
            dict.fromkeys(
                limitation
                for insight in self.insights
                for limitation in _string_list(insight.get("limitations"))
            )
        )
        structured_findings = [
            {
                "title": _text(insight.get("title")),
                "finding": _text(insight.get("finding")),
                "calculation": _text(insight.get("calculation")),
                "supporting_data": _dict_rows(insight.get("supporting_data"))[:12],
            }
            for insight in self.insights
        ]
        supporting_data = tuple(
            row
            for insight in self.insights
            for row in _dict_rows(insight.get("supporting_data"))
        )[:12]
        return {
            "id": self.evidence_id,
            "insight_id": f"CHAT-{self.evidence_id.removeprefix('EVD-')}",
            "insight_type": "chat_qna",
            "metric_id": "DATASET",
            "metric": "Saved chat Q&A",
            "kpi_definition": {
                "name": "Saved data-chat answer",
                "calculation": "Verified Python and DuckDB query evidence",
            },
            "source": self.source,
            "source_columns": list(columns),
            "filters": {},
            "periods": [],
            "calculation_description": (
                f"Saved question: {self.question} The answer was assembled from "
                "the attached Python/DuckDB-calculated findings."
            ),
            "observation": {
                "question": self.question,
                "verified_answer": self.verified_answer,
                "findings": structured_findings,
            },
            "supporting_data": list(supporting_data),
            "record_count": 0,
            "ranking": {
                "impact": 0.5,
                "confidence": 0.75,
                "relevance": 0.75,
                "combined": 0.65,
                "rank": rank,
            },
            "chart": None,
            "limitations": list(limitations),
        }


def save_chat_evidence(
    *,
    dataset_id: str,
    source: dict[str, object],
    question: str,
    verified_answer: str,
    displayed_answer: str,
    insights: tuple[QueryInsight, ...],
    chat_dir: Path,
) -> SavedChatEvidence:
    """Atomically retain one verified question, answer, and evidence bundle."""

    source_sha256 = _text(source.get("sha256"))
    if (
        _DATASET_ID.fullmatch(dataset_id) is None
        or not source_sha256
        or not question.strip()
        or not verified_answer.strip()
        or not insights
    ):
        raise SavedChatEvidenceError("Verified chat evidence is incomplete.")
    insight_payload = tuple(insight.to_dict() for insight in insights[:8])
    identity = json.dumps(
        {
            "dataset_id": dataset_id,
            "source_sha256": source_sha256,
            "question": question.strip(),
            "verified_answer": verified_answer.strip(),
            "insights": insight_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest().upper()
    artifact = SavedChatEvidence(
        schema_version=1,
        dataset_id=dataset_id,
        evidence_id=f"EVD-{digest[:16]}",
        source_sha256=source_sha256,
        source=dict(source),
        question=question.strip()[:1_000],
        verified_answer=verified_answer.strip()[:4_000],
        displayed_answer=displayed_answer.strip()[:4_000],
        insights=insight_payload,
        saved_at=datetime.now(UTC).isoformat(),
    )
    encoded = json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    if len(encoded.encode("utf-8")) > _MAX_ARTIFACT_BYTES:
        raise SavedChatEvidenceError("Chat evidence is too large to save safely.")
    directory = chat_dir / dataset_id
    directory.mkdir(parents=True, exist_ok=True)
    final_path = directory / f"{artifact.evidence_id}.json"
    temporary_path = directory / f".{artifact.evidence_id}.{secrets.token_hex(8)}.part"
    try:
        temporary_path.write_text(f"{encoded}\n", encoding="utf-8")
        temporary_path.replace(final_path)
    except Exception as error:
        temporary_path.unlink(missing_ok=True)
        raise SavedChatEvidenceError("Chat evidence could not be saved.") from error
    return artifact


def list_saved_chat_evidence(
    *,
    dataset_id: str,
    source_sha256s: frozenset[str],
    chat_dir: Path,
) -> tuple[SavedChatEvidence, ...]:
    """Load retained Q&A that still belongs to the current dataset source."""

    if _DATASET_ID.fullmatch(dataset_id) is None:
        raise SavedChatEvidenceError("Chat dataset ID is invalid.")
    directory = chat_dir / dataset_id
    if not directory.is_dir():
        return ()
    artifacts: list[SavedChatEvidence] = []
    for path in sorted(directory.glob("EVD-*.json"))[:_MAX_SAVED_QUESTIONS]:
        artifact = _load(path, dataset_id=dataset_id)
        if artifact.source_sha256 in source_sha256s:
            artifacts.append(artifact)
    return tuple(sorted(artifacts, key=lambda item: item.saved_at, reverse=True))


def merge_chat_evidence(
    evidence_payload: dict[str, object] | None,
    *,
    dataset_id: str,
    sources: tuple[dict[str, object], ...],
    artifacts: tuple[SavedChatEvidence, ...],
) -> dict[str, object] | None:
    """Append saved Q&A to the live evidence payload for report selection."""

    if not artifacts:
        return evidence_payload
    existing = evidence_payload.get("records") if evidence_payload else []
    if not isinstance(existing, list) or not all(isinstance(row, dict) for row in existing):
        raise SavedChatEvidenceError("Current deterministic evidence is invalid.")
    records = list(existing)
    existing_ids = {str(record.get("id")) for record in records}
    for offset, artifact in enumerate(artifacts, start=1):
        if artifact.evidence_id not in existing_ids:
            records.append(artifact.evidence_record(rank=len(records) + offset))
    return {
        "schema_version": 2,
        "dataset_id": dataset_id,
        "sources": list(sources),
        "records": records,
    }


def _load(path: Path, *, dataset_id: str) -> SavedChatEvidence:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SavedChatEvidenceError("Saved chat evidence is unreadable.") from error
    if not isinstance(payload, dict):
        raise SavedChatEvidenceError("Saved chat evidence has an invalid shape.")
    evidence_id = _text(payload.get("evidence_id"))
    source = payload.get("source")
    raw_insights = payload.get("insights")
    if (
        payload.get("schema_version") != 1
        or payload.get("dataset_id") != dataset_id
        or _EVIDENCE_ID.fullmatch(evidence_id) is None
        or path.stem != evidence_id
        or not isinstance(source, dict)
        or not isinstance(raw_insights, list)
        or not raw_insights
        or not all(isinstance(insight, dict) for insight in raw_insights)
    ):
        raise SavedChatEvidenceError("Saved chat evidence has an invalid shape.")
    artifact = SavedChatEvidence(
        schema_version=1,
        dataset_id=dataset_id,
        evidence_id=evidence_id,
        source_sha256=_text(payload.get("source_sha256")),
        source=dict(source),
        question=_text(payload.get("question")),
        verified_answer=_text(payload.get("verified_answer")),
        displayed_answer=_text(payload.get("displayed_answer")),
        insights=tuple(dict(insight) for insight in raw_insights),
        saved_at=_text(payload.get("saved_at")),
    )
    if not all(
        (
            artifact.source_sha256,
            artifact.question,
            artifact.verified_answer,
            artifact.saved_at,
        )
    ):
        raise SavedChatEvidenceError("Saved chat evidence has an invalid shape.")
    return artifact


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _dict_rows(value: object) -> list[dict[str, object]]:
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
