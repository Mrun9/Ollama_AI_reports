"""Persistence and report-contract tests for saved data-chat Q&A."""

from pathlib import Path

from insight_reporter.query_insight_engine import QueryInsight
from insight_reporter.saved_chat_evidence import (
    list_saved_chat_evidence,
    merge_chat_evidence,
    save_chat_evidence,
)


def _insight() -> QueryInsight:
    return QueryInsight(
        insight_type="statistical_relationship",
        title="Fuel efficiency by fuel type",
        finding=(
            "Fuel efficiency is associated with fuel type "
            "(one-way ANOVA: f_statistic=8.4, p=0.004)."
        ),
        columns_used=("fuel_type", "fuel_efficiency"),
        calculation="one_way_anova",
        supporting_data=(
            {
                "test": "one-way ANOVA",
                "f_statistic": 8.4,
                "p_value": 0.004,
                "significant_at_0.05": True,
                "sample_size": 48,
            },
        ),
        relevance_score=0.99,
        limitations=("Statistical association does not prove causation.",),
    )


def test_saved_chat_qna_keeps_verified_evidence_and_merges_for_reports(
    tmp_path: Path,
) -> None:
    dataset_id = "a" * 32
    source = {"sha256": "source-hash", "format": "csv", "filename": "data.csv"}

    saved = save_chat_evidence(
        dataset_id=dataset_id,
        source=source,
        question="Does fuel type affect fuel efficiency?",
        verified_answer="Fuel efficiency is associated with fuel type (p=0.004).",
        displayed_answer="Fuel type and fuel efficiency are associated (p=0.004).",
        insights=(_insight(),),
        chat_dir=tmp_path,
    )
    loaded = list_saved_chat_evidence(
        dataset_id=dataset_id,
        source_sha256s=frozenset({"source-hash"}),
        chat_dir=tmp_path,
    )
    merged = merge_chat_evidence(
        None,
        dataset_id=dataset_id,
        sources=(source,),
        artifacts=loaded,
    )

    assert loaded == (saved,)
    assert merged is not None
    record = merged["records"][0]
    assert record["id"] == saved.evidence_id
    assert record["insight_type"] == "chat_qna"
    assert record["metric_id"] == "DATASET"
    assert record["observation"]["question"] == "Does fuel type affect fuel efficiency?"
    finding = record["observation"]["findings"][0]
    assert finding["calculation"] == "one_way_anova"
    assert finding["supporting_data"][0]["p_value"] == 0.004


def test_saved_chat_qna_is_excluded_after_source_changes(tmp_path: Path) -> None:
    dataset_id = "b" * 32
    save_chat_evidence(
        dataset_id=dataset_id,
        source={"sha256": "old-source"},
        question="What changed?",
        verified_answer="The verified answer.",
        displayed_answer="The verified answer.",
        insights=(_insight(),),
        chat_dir=tmp_path,
    )

    loaded = list_saved_chat_evidence(
        dataset_id=dataset_id,
        source_sha256s=frozenset({"new-source"}),
        chat_dir=tmp_path,
    )

    assert loaded == ()


def test_floating_chat_has_visible_close_and_report_selection_action() -> None:
    project = Path(__file__).resolve().parents[1]
    template = (project / "src/insight_reporter/templates/_dataset_chat_widget.html").read_text(
        encoding="utf-8"
    )
    script = (project / "src/insight_reporter/static/dataset_chat_widget.js").read_text(
        encoding="utf-8"
    )

    assert "data-chat-close" in template
    assert "Close data chat" in template
    assert "event.key === \"Escape\"" in script
    assert "Choose it for the final report" in script
    assert "/chat/history" in script
    assert "Restored saved chat history" in script
