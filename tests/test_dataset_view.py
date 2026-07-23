"""Tests for the source-aware dataset abstraction."""

from pathlib import Path

import pytest

from insight_reporter.dataset_view import (
    CsvDatasetView,
    DatasetViewError,
    source_id_from_hash,
)


def test_csv_dataset_view_exposes_stable_manifest_and_rows(tmp_path: Path) -> None:
    path = tmp_path / "source.csv"
    path.write_text("region,revenue\nNorth,10\nSouth,20\n", encoding="utf-8")

    first = CsvDatasetView.from_path(path)
    second = CsvDatasetView.from_path(path)

    assert first.headers == ("region", "revenue")
    assert first.sources == second.sources
    assert first.sources[0].source_id == source_id_from_hash(first.sources[0].sha256)
    assert first.sources[0].row_count == 2
    assert first.iter_rows()[0].values == {"region": "North", "revenue": "10"}


def test_csv_dataset_view_rejects_changed_width(tmp_path: Path) -> None:
    path = tmp_path / "broken.csv"
    path.write_text("region,revenue\nNorth,10\nSouth\n", encoding="utf-8")

    with pytest.raises(DatasetViewError, match="Malformed CSV row"):
        CsvDatasetView.from_path(path)
