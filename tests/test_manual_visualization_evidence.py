"""Deterministic evidence tests for manual visualizations."""

from pathlib import Path

import pytest

from insight_reporter.business_config import validate_business_configuration
from insight_reporter.dataset_profile import profile_csv
from insight_reporter.dataset_view import CsvDatasetView
from insight_reporter.manual_visualization_evidence import (
    generate_manual_visualization_evidence,
)
from insight_reporter.visualization_builder import (
    build_visualization,
    parse_visualization_spec,
    save_visualization,
)


def _assets(tmp_path: Path):  # type: ignore[no-untyped-def]
    dataset_id = "d" * 32
    path = tmp_path / f"{dataset_id}.csv"
    path.write_text(
        "date,segment,revenue,cost\n"
        "2026-01-01,A,100,60\n"
        "2026-01-02,B,200,120\n"
        "2026-02-01,A,150,80\n"
        "2026-02-02,B,250,140\n"
        "2026-03-01,A,180,90\n"
        "2026-03-02,B,300,160\n",
        encoding="utf-8",
    )
    view = CsvDatasetView.from_path(path)
    profile = profile_csv(path)
    configuration = validate_business_configuration(
        profile,
        dataset_id=dataset_id,
        primary_kpi="revenue",
        kpi_direction="higher",
        date_column="date",
        category_columns=["segment"],
        target_or_benchmark="",
        business_objective="Review revenue performance.",
    )
    return view, profile, configuration


def _spec(**overrides):  # type: ignore[no-untyped-def]
    values = {
        "title": "Revenue by segment",
        "purpose": "Which segment has the highest revenue?",
        "chart_type": "category_bar",
        "measure_selectors": [],
        "x_column": "segment",
        "series_column": "",
        "aggregation": "sum",
        "date_granularity": "month",
        "filter_column": "",
        "filter_mode": "include",
        "filter_values": "",
        "date_start": "",
        "date_end": "",
        "sort_by": "value",
        "sort_direction": "descending",
        "top_n": "10",
        "scale": "linear",
        "bin_count": "10",
        "include_in_report": "yes",
        "replaces_visualization_id": "",
    }
    values.update(overrides)
    return parse_visualization_spec(values)


def test_category_chart_produces_stable_extreme_evidence(
    tmp_path: Path,
) -> None:
    view, profile, configuration = _assets(tmp_path)
    artifact = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=_spec(
            measure_selectors=[
                f"metric:{configuration.primary_metric_id}"
            ]
        ),
        chart_dir=tmp_path / "charts",
    )
    saved, _path = save_visualization(
        artifact,
        visualization_dir=tmp_path / "visualizations",
    )

    first = generate_manual_visualization_evidence(saved)
    second = generate_manual_visualization_evidence(saved)

    assert first == second
    assert first.id.startswith("MVE-")
    assert first.purpose_source == "user_provided"
    assert first.required_metric_ids == (
        configuration.primary_metric_id,
    )
    extremes = first.observations[0]
    assert extremes["type"] == "displayed_extremes"
    assert extremes["observation"]["highest"]["x"] == "B"
    assert extremes["observation"]["highest"]["value"] == 750
    assert extremes["observation"]["lowest"]["x"] == "A"
    assert extremes["observation"]["lowest"]["value"] == 430
    assert "row_number" not in {
        key for row in first.supporting_data for key in row
    }


def test_scatter_chart_labels_python_correlation_as_association(
    tmp_path: Path,
) -> None:
    view, profile, configuration = _assets(tmp_path)
    artifact = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=_spec(
            title="Cost and revenue",
            purpose="How are cost and revenue associated?",
            chart_type="scatter",
            measure_selectors=[
                f"metric:{configuration.primary_metric_id}"
            ],
            x_column="cost",
            aggregation="configured",
        ),
        chart_dir=tmp_path / "charts",
    )
    saved, _path = save_visualization(
        artifact,
        visualization_dir=tmp_path / "visualizations",
    )

    evidence = generate_manual_visualization_evidence(saved)
    observation = evidence.observations[0]

    assert observation["type"] == "numeric_association"
    assert observation["observation"]["label"] == (
        "association_not_causation"
    )
    assert observation["observation"]["coefficient"] == pytest.approx(
        0.986,
        abs=0.01,
    )
    assert all(
        "row_number" not in row for row in evidence.supporting_data
    )
