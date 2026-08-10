from pathlib import Path

from insight_reporter.dataset_chat import answer_dataset_question
from insight_reporter.dataset_profile import profile_dataset
from insight_reporter.dataset_view import load_dataset_view


def _profiled_view(path: Path):  # type: ignore[no-untyped-def]
    view = load_dataset_view(path)
    profile = profile_dataset(view, size_bytes=path.stat().st_size)
    return view, profile


def test_dataset_chat_answers_boolean_rate_by_named_dimension(tmp_path: Path) -> None:
    path = tmp_path / "hr.csv"
    path.write_text(
        "\n".join(
            (
                "Department,Attrition,MonthlyIncome",
                "Sales,Yes,100",
                "Sales,No,200",
                "Engineering,No,300",
                "Engineering,No,400",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)

    turn = answer_dataset_question(
        "Which departments have the highest attrition rate?",
        view=view,
        profile=profile,
    )

    assert turn.analysis_request.intent == "boolean_rate"
    assert turn.analysis_request.target_columns == ("Attrition",)
    assert turn.analysis_request.dimension_columns == ("Department",)
    assert "Sales has the highest Attrition rate" in turn.answer
    assert "50%" in turn.answer


def test_dataset_chat_matches_camel_case_metric_without_age_substring(tmp_path: Path) -> None:
    path = tmp_path / "income.csv"
    path.write_text(
        "\n".join(
            (
                "Department,Age,MonthlyIncome",
                "Sales,25,100",
                "Sales,35,200",
                "Engineering,40,500",
                "Engineering,45,700",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)

    turn = answer_dataset_question(
        "Which groups have the highest average monthly income?",
        view=view,
        profile=profile,
    )

    assert turn.analysis_request.intent == "top_bottom"
    assert turn.analysis_request.metric_columns[0] == "MonthlyIncome"
    assert "MonthlyIncome" in turn.answer
    assert "Age" not in turn.answer


def test_dataset_chat_reports_missingness_without_model(tmp_path: Path) -> None:
    path = tmp_path / "missing.csv"
    path.write_text(
        "\n".join(
            (
                "Region,Value",
                "North,10",
                "South,",
                "East,30",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)

    turn = answer_dataset_question(
        "Are there missing values?",
        view=view,
        profile=profile,
    )

    assert turn.model_status == "deterministic_only"
    assert "Value has the highest missingness" in turn.answer
