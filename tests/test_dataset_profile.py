"""Manually calculated expectations for deterministic dataset profiling."""

from pathlib import Path

import pytest

from insight_reporter.dataset_profile import ColumnType, DatasetProfile, profile_csv


def _profile(tmp_path: Path, content: str) -> DatasetProfile:
    path = tmp_path / "dataset.csv"
    path.write_text(content, encoding="utf-8")
    return profile_csv(path)


def test_numeric_columns_and_statistics_are_correct(tmp_path: Path) -> None:
    profile = _profile(
        tmp_path,
        "region,amount\nNorth,10\nSouth,20\nNorth,30\n",
    )
    amount = profile.column("amount")

    assert profile.row_count == 3
    assert profile.column_count == 2
    assert amount is not None
    assert amount.inferred_type is ColumnType.NUMERIC
    assert amount.numeric_statistics is not None
    assert amount.numeric_statistics.count == 3
    assert amount.numeric_statistics.minimum == 10
    assert amount.numeric_statistics.maximum == 30
    assert amount.numeric_statistics.mean == 20
    assert amount.numeric_statistics.median == 20
    assert amount.numeric_statistics.total == 60
    assert amount.numeric_statistics.standard_deviation == pytest.approx(8.1649658093)
    assert profile.kpi_candidates == ("amount",)


def test_dates_are_recognized_without_converting_identifiers(tmp_path: Path) -> None:
    profile = _profile(
        tmp_path,
        (
            "record_id,event_date,revenue\n"
            "20260101,2026-01-01,100\n"
            "20260102,2026-01-02,120\n"
            "20260103,2026-01-03,140\n"
        ),
    )
    record_id = profile.column("record_id")
    event_date = profile.column("event_date")

    assert record_id is not None
    assert record_id.inferred_type is ColumnType.IDENTIFIER
    assert event_date is not None
    assert event_date.inferred_type is ColumnType.DATETIME
    assert event_date.date_range is not None
    assert event_date.date_range.earliest == "2026-01-01T00:00:00"
    assert event_date.date_range.latest == "2026-01-03T00:00:00"
    assert profile.date_candidates == ("event_date",)
    assert profile.kpi_candidates == ("revenue",)


def test_identifiers_are_not_measurable_kpis(tmp_path: Path) -> None:
    profile = _profile(
        tmp_path,
        "customer_number,revenue\n10001,50\n10002,75\n10003,100\n",
    )

    assert profile.column("customer_number").inferred_type is ColumnType.IDENTIFIER  # type: ignore[union-attr]
    assert "customer_number" not in profile.kpi_candidates
    assert profile.kpi_candidates == ("revenue",)


def test_missing_constant_and_empty_columns_are_identified(tmp_path: Path) -> None:
    profile = _profile(
        tmp_path,
        "status,empty,amount\nOpen,,10\nOpen,NA,\nOpen,null,30\n",
    )
    status = profile.column("status")
    empty = profile.column("empty")
    amount = profile.column("amount")

    assert status is not None
    assert status.is_constant is True
    assert status.is_empty is False
    assert status.unique_count == 1
    assert empty is not None
    assert empty.inferred_type is ColumnType.EMPTY
    assert empty.is_empty is True
    assert empty.missing_count == 3
    assert empty.unique_count == 0
    assert amount is not None
    assert amount.missing_count == 1
    assert amount.missing_percentage == pytest.approx(100 / 3)
    assert amount.unique_count == 2


def test_boolean_categorical_and_free_text_are_detected(tmp_path: Path) -> None:
    profile = _profile(
        tmp_path,
        (
            "active,segment,notes\n"
            "true,A,This is a deliberately long explanatory sentence for profiling.\n"
            "false,B,Another deliberately long explanatory sentence for profiling.\n"
            "yes,A,This third explanatory sentence is also intentionally quite long.\n"
            "no,B,The final explanatory sentence remains long enough for free text.\n"
        ),
    )

    assert profile.column("active").inferred_type is ColumnType.BOOLEAN  # type: ignore[union-attr]
    assert profile.column("segment").inferred_type is ColumnType.CATEGORICAL  # type: ignore[union-attr]
    assert profile.column("notes").inferred_type is ColumnType.FREE_TEXT  # type: ignore[union-attr]
    assert profile.category_candidates == ("active", "segment")


def test_dataset_without_date_column_is_handled(tmp_path: Path) -> None:
    profile = _profile(
        tmp_path,
        "account_id,score,group\nA-1,10,East\nA-2,20,West\nA-3,30,East\n",
    )

    assert profile.date_candidates == ()
    assert profile.kpi_candidates == ("score",)


def test_basic_sales_fixture_matches_manual_profile() -> None:
    path = Path(__file__).resolve().parents[1] / "sample_data" / "basic_sales.csv"
    profile = profile_csv(path)
    revenue = profile.column("revenue")
    date = profile.column("date")

    assert profile.row_count == 6
    assert profile.column_count == 6
    assert profile.kpi_candidates == ("revenue", "cost", "units")
    assert profile.date_candidates == ("date",)
    assert profile.category_candidates == ("region", "product")
    assert revenue is not None
    assert revenue.numeric_statistics is not None
    assert revenue.numeric_statistics.minimum == 8_900
    assert revenue.numeric_statistics.maximum == 15_100
    assert revenue.numeric_statistics.total == 72_200
    assert revenue.numeric_statistics.mean == pytest.approx(12_033.333333333334)
    assert date is not None
    assert date.date_range is not None
    assert date.date_range.earliest == "2026-01-01T00:00:00"
    assert date.date_range.latest == "2026-01-06T00:00:00"
