"""Milestone 4B manual visualization correctness and route tests."""

import io
import json
import re
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from openpyxl import Workbook

from insight_reporter.business_config import (
    validate_business_configuration,
    validate_derived_business_configuration,
)
from insight_reporter.dataset_profile import profile_csv, profile_dataset
from insight_reporter.dataset_view import (
    CsvDatasetView,
    load_dataset_view,
    source_id_from_hash,
)
from insight_reporter.derived_metrics import validate_formula_metric
from insight_reporter.visualization_builder import (
    VisualizationError,
    build_visualization,
    list_visualizations,
    load_visualization,
    parse_visualization_spec,
    save_visualization,
)


def _dataset(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "dataset.csv"
    path.write_text(
        (
            "date,segment,revenue,cost,stress,net,note\n"
            "2026-01-01,A,100,60,2,-5,alpha\n"
            "2026-01-02,B,200,120,4,0,beta\n"
            "2026-02-01,A,150,80,6,5,gamma\n"
            "2026-02-02,B,250,140,8,10,delta\n"
            "2026-03-01,A,180,90,10,15,epsilon\n"
            "2026-03-02,B,300,160,6,20,zeta\n"
        ),
        encoding="utf-8",
    )
    profile = profile_csv(path)
    view = CsvDatasetView.from_path(path)
    configuration = validate_business_configuration(
        profile,
        dataset_id="a" * 32,
        primary_kpi="revenue",
        secondary_kpis=["cost"],
        kpi_direction="higher",
        date_column="date",
        category_columns=["segment"],
        target_or_benchmark="",
        business_objective="Review configured and supplementary visualizations.",
    )
    return path, view, profile, configuration


def _spec(**overrides):  # type: ignore[no-untyped-def]
    values = {
        "title": "Stress by segment",
        "purpose": "Compare average stress by segment.",
        "chart_type": "category_bar",
        "measure_selectors": ["column:stress"],
        "x_column": "segment",
        "series_column": "",
        "aggregation": "mean",
        "date_granularity": "month",
        "filter_column": "",
        "filter_mode": "include",
        "filter_values": "",
        "date_start": "",
        "date_end": "",
        "sort_by": "label",
        "sort_direction": "ascending",
        "top_n": "10",
        "scale": "linear",
        "bin_count": "10",
        "include_in_report": "yes",
        "replaces_visualization_id": "",
    }
    values.update(overrides)
    return parse_visualization_spec(values)


def test_supplementary_numeric_and_record_count_charts_are_traceable(
    tmp_path: Path,
) -> None:
    _path, view, profile, configuration = _dataset(tmp_path)
    supplementary = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=_spec(),
        chart_dir=tmp_path / "charts",
    )
    count = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=_spec(
            title="Records by month",
            chart_type="time_line",
            measure_selectors=["count:records"],
            x_column="date",
            date_granularity="month",
            filter_column="segment",
            filter_values="A",
        ),
        chart_dir=tmp_path / "charts",
    )
    excluded = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=_spec(
            title="Records excluding A",
            measure_selectors=["count:records"],
            filter_column="segment",
            filter_mode="exclude",
            filter_values="A",
        ),
        chart_dir=tmp_path / "charts",
    )
    top_one = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=_spec(
            title="Largest revenue segment",
            measure_selectors=[
                f"metric:{configuration.primary_metric_id}"
            ],
            aggregation="sum",
            sort_by="value",
            sort_direction="descending",
            top_n="1",
        ),
        chart_dir=tmp_path / "charts",
    )

    assert supplementary.classification == "supplementary"
    assert supplementary.spec.include_in_report is True
    assert supplementary.source_columns == ("segment", "stress")
    assert supplementary.supporting_data == (
        {
            "x": "A",
            "series": None,
            "measure_selector": "column:stress",
            "measure": "stress",
            "aggregation": "mean",
            "value": 6.0,
            "record_count": 3,
        },
        {
            "x": "B",
            "series": None,
            "measure_selector": "column:stress",
            "measure": "stress",
            "aggregation": "mean",
            "value": 6.0,
            "record_count": 3,
        },
    )
    assert [row["value"] for row in count.supporting_data] == [1.0, 1.0, 1.0]
    assert count.filtered_record_count == 3
    assert excluded.filtered_record_count == 3
    assert {row["x"] for row in excluded.supporting_data} == {"B"}
    assert [row["x"] for row in top_one.supporting_data] == ["B"]
    for artifact in (supplementary, count):
        chart_path = tmp_path / "charts" / artifact.chart.filename
        assert re.fullmatch(r"[0-9a-f]{32}\.png", artifact.chart.filename)
        assert chart_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_configured_multiple_kpis_and_aggregate_formula_are_grouped_exactly(
    tmp_path: Path,
) -> None:
    _path, view, profile, configuration = _dataset(tmp_path)
    selectors = [f"metric:{metric.metric_id}" for metric in configuration.metrics]
    multiple = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=_spec(
            title="Revenue and cost",
            measure_selectors=selectors,
            aggregation="sum",
        ),
        chart_dir=tmp_path / "charts",
    )
    derived = validate_formula_metric(
        profile,
        name="Revenue cost ratio",
        formula="SUM([revenue]) / SUM([cost]) * 100",
        calculation_level="aggregate",
        aggregation="formula",
        display_format="percentage",
        source_id=source_id_from_hash(
            profile.source_sha256,
            profile.source_table_name,
        ),
    )
    derived_configuration = validate_derived_business_configuration(
        profile,
        dataset_id="a" * 32,
        derived_metric=derived,
        kpi_direction="higher",
        date_column="date",
        category_columns=["segment"],
        target_or_benchmark="",
        business_objective="Compare the aggregate ratio by segment.",
    )
    ratio_selector = f"metric:{derived_configuration.primary_metric_id}"
    ratio = build_visualization(
        view,
        profile=profile,
        configuration=derived_configuration,
        spec=_spec(
            title="Ratio by segment",
            measure_selectors=[ratio_selector],
            aggregation="configured",
        ),
        chart_dir=tmp_path / "charts",
    )

    assert multiple.classification == "kpi"
    assert len(multiple.supporting_data) == 4
    by_measure_and_segment = {
        (row["measure"], row["x"]): row["value"]
        for row in multiple.supporting_data
    }
    assert by_measure_and_segment[("revenue", "A")] == 430
    assert by_measure_and_segment[("cost", "B")] == 420
    ratio_by_segment = {
        row["x"]: row["value"] for row in ratio.supporting_data
    }
    assert ratio_by_segment["A"] == pytest.approx((430 / 230) * 100)
    assert ratio_by_segment["B"] == pytest.approx((750 / 420) * 100)
    assert ratio.measures[0].effective_aggregation == "formula"

    mixed_configuration = validate_derived_business_configuration(
        profile,
        dataset_id="a" * 32,
        derived_metric=derived,
        kpi_direction="higher",
        date_column="date",
        category_columns=["segment"],
        target_or_benchmark="",
        business_objective="Compare configured measures.",
        existing_configuration=configuration,
        metric_role="secondary",
    )
    ratio_metric = next(
        metric
        for metric in mixed_configuration.metrics
        if metric.name == "Revenue cost ratio"
    )
    with pytest.raises(VisualizationError, match="display format"):
        build_visualization(
            view,
            profile=profile,
            configuration=mixed_configuration,
            spec=_spec(
                measure_selectors=[
                    f"metric:{mixed_configuration.primary_metric_id}",
                    f"metric:{ratio_metric.metric_id}",
                ]
            ),
            chart_dir=tmp_path / "charts",
        )


def test_row_level_charts_and_unsafe_combinations_are_validated(
    tmp_path: Path,
) -> None:
    _path, view, profile, configuration = _dataset(tmp_path)
    revenue_selector = f"metric:{configuration.primary_metric_id}"
    scatter = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=_spec(
            title="Stress and revenue",
            chart_type="scatter",
            measure_selectors=[revenue_selector],
            x_column="stress",
            series_column="segment",
        ),
        chart_dir=tmp_path / "charts",
    )
    histogram = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=_spec(
            title="Stress distribution",
            chart_type="histogram",
            measure_selectors=["column:stress"],
            x_column="",
        ),
        chart_dir=tmp_path / "charts",
    )

    assert len(scatter.supporting_data) == 6
    assert scatter.supporting_data[0] == {
        "row_number": 2,
        "x": 2.0,
        "y": 100.0,
        "series": "A",
    }
    assert len(histogram.supporting_data) == 6

    with pytest.raises(VisualizationError, match="Logarithmic"):
        build_visualization(
            view,
            profile=profile,
            configuration=configuration,
            spec=_spec(
                measure_selectors=["column:net"],
                aggregation="min",
                scale="log",
            ),
            chart_dir=tmp_path / "charts",
        )
    with pytest.raises(VisualizationError, match="category column"):
        build_visualization(
            view,
            profile=profile,
            configuration=configuration,
            spec=_spec(x_column="note"),
            chart_dir=tmp_path / "charts",
        )
    with pytest.raises(VisualizationError, match="do not exist"):
        build_visualization(
            view,
            profile=profile,
            configuration=configuration,
            spec=_spec(filter_column="segment", filter_values="Missing"),
            chart_dir=tmp_path / "charts",
        )


def test_aggregate_formula_is_rejected_for_row_level_scatter(tmp_path: Path) -> None:
    _path, view, profile, _configuration = _dataset(tmp_path)
    metric = validate_formula_metric(
        profile,
        name="Margin",
        formula="(SUM([revenue]) - SUM([cost])) / SUM([revenue]) * 100",
        calculation_level="aggregate",
        aggregation="formula",
        display_format="percentage",
        source_id=source_id_from_hash(
            profile.source_sha256,
            profile.source_table_name,
        ),
    )
    configuration = validate_derived_business_configuration(
        profile,
        dataset_id="a" * 32,
        derived_metric=metric,
        kpi_direction="higher",
        date_column="date",
        category_columns=["segment"],
        target_or_benchmark="",
        business_objective="Review margin.",
    )

    with pytest.raises(VisualizationError, match="row-level"):
        build_visualization(
            view,
            profile=profile,
            configuration=configuration,
            spec=_spec(
                chart_type="scatter",
                measure_selectors=[f"metric:{configuration.primary_metric_id}"],
                x_column="stress",
            ),
            chart_dir=tmp_path / "charts",
        )


def test_saved_visualization_is_reopened_and_tampering_is_rejected(
    tmp_path: Path,
) -> None:
    _path, view, profile, configuration = _dataset(tmp_path)
    artifact = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=_spec(),
        chart_dir=tmp_path / "charts",
    )
    saved, path = save_visualization(
        artifact,
        visualization_dir=tmp_path / "visualizations",
    )
    loaded = load_visualization(
        saved.visualization_id or "",
        dataset_id="a" * 32,
        visualization_dir=tmp_path / "visualizations",
        profile=profile,
        configuration=configuration,
    )

    assert loaded.to_dict() == saved.to_dict()
    assert loaded.schema_version == 3
    assert loaded.spec.purpose == "Compare average stress by segment."
    assert list_visualizations(
        dataset_id="a" * 32,
        visualization_dir=tmp_path / "visualizations",
        profile=profile,
        configuration=configuration,
    ) == (saved,)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["spec"]["measure_selectors"] = ["column:does_not_exist"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(VisualizationError):
        load_visualization(
            saved.visualization_id or "",
            dataset_id="a" * 32,
            visualization_dir=tmp_path / "visualizations",
            profile=profile,
            configuration=configuration,
        )


def test_version_two_visualization_without_purpose_remains_loadable(
    tmp_path: Path,
) -> None:
    _path, view, profile, configuration = _dataset(tmp_path)
    artifact = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=_spec(),
        chart_dir=tmp_path / "charts",
    )
    saved, path = save_visualization(
        artifact,
        visualization_dir=tmp_path / "visualizations",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    payload["spec"].pop("purpose")
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_visualization(
        saved.visualization_id or "",
        dataset_id="a" * 32,
        visualization_dir=tmp_path / "visualizations",
        profile=profile,
        configuration=configuration,
    )

    assert loaded.schema_version == 2
    assert loaded.spec.purpose == ""


@pytest.mark.parametrize("source_format", ["json", "xlsx"])
def test_visualizations_are_format_independent(
    tmp_path: Path,
    source_format: str,
) -> None:
    if source_format == "json":
        path = tmp_path / "dataset.json"
        path.write_text(
            json.dumps(
                [
                    {"segment": "A", "revenue": 10},
                    {"segment": "A", "revenue": 20},
                    {"segment": "B", "revenue": 30},
                    {"segment": "B", "revenue": 40},
                ]
            ),
            encoding="utf-8",
        )
        view = load_dataset_view(path)
    else:
        path = tmp_path / "dataset.xlsx"
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Data"
        worksheet.append(["segment", "revenue"])
        worksheet.append(["A", 10])
        worksheet.append(["A", 20])
        worksheet.append(["B", 30])
        worksheet.append(["B", 40])
        workbook.save(path)
        view = load_dataset_view(path, table_name="Data")
    profile = profile_dataset(view, size_bytes=path.stat().st_size)
    configuration = validate_business_configuration(
        profile,
        dataset_id="b" * 32,
        primary_kpi="revenue",
        kpi_direction="higher",
        date_column="",
        category_columns=["segment"],
        target_or_benchmark="",
        business_objective="Compare revenue.",
    )
    artifact = build_visualization(
        view,
        profile=profile,
        configuration=configuration,
        spec=_spec(measure_selectors=["count:records"]),
        chart_dir=tmp_path / "charts",
    )

    assert artifact.source["format"] == source_format
    assert artifact.source["worksheet"] == ("Data" if source_format == "xlsx" else None)
    assert [row["value"] for row in artifact.supporting_data] == [2.0, 2.0]


def _upload_and_configure(client: FlaskClient) -> str:
    content = (
        b"date,segment,revenue,stress\n"
        b"2026-01-01,A,100,2\n"
        b"2026-01-02,B,200,4\n"
        b"2026-02-01,A,150,6\n"
        b"2026-02-02,B,250,8\n"
        b"2026-03-01,A,180,10\n"
        b"2026-03-02,B,300,6\n"
    )
    uploaded = client.post(
        "/upload",
        data={"file": (io.BytesIO(content), "visualization.csv", "text/csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    match = re.search(rb"<dd>([0-9a-f]{32})\.csv</dd>", uploaded.data)
    assert match is not None
    dataset_id = match.group(1).decode("ascii")
    configured = client.post(
        f"/configure/{dataset_id}",
        data={
            "primary_kpi": "revenue",
            "kpi_direction": "higher",
            "date_column": "date",
            "category_columns": ["segment"],
            "target_or_benchmark": "",
            "business_objective": "Review manual visualizations.",
        },
    )
    assert configured.status_code == 303
    return dataset_id


def test_supplementary_preview_save_reopen_regenerate_and_edit_workflow(
    app: Flask,
    client: FlaskClient,
) -> None:
    dataset_id = _upload_and_configure(client)
    builder = client.get(f"/visualizations/{dataset_id}/new")
    assert builder.status_code == 200
    assert b"Build a manual visualization" in builder.data
    assert b"What question should this visualization answer?" in builder.data
    assert b"Supplementary numeric column: stress" in builder.data
    assert b"Describe what you want to visualize" not in builder.data
    assert b"Generate validated preview with Ollama" not in builder.data
    assert (
        client.post(f"/visualizations/{dataset_id}/assistant").status_code
        == 405
    )

    preview_post = client.post(
        f"/visualizations/{dataset_id}/preview",
        data={
            "title": "<script>Stress by segment</script>",
            "purpose": "<script>Which segment is highest?</script>",
            "chart_type": "category_bar",
            "measure_selectors": ["column:stress"],
            "x_column": "segment",
            "series_column": "",
            "aggregation": "mean",
            "date_granularity": "month",
            "filter_column": "",
            "filter_mode": "include",
            "filter_values": "",
            "date_start": "",
            "date_end": "",
            "sort_by": "label",
            "sort_direction": "ascending",
            "top_n": "10",
            "scale": "linear",
            "bin_count": "10",
            "include_in_report": "yes",
        },
    )
    assert preview_post.status_code == 303
    preview_url = preview_post.headers["Location"]
    preview = client.get(preview_url)
    assert preview.status_code == 200
    assert b"Supplementary visualization" in preview.data
    assert b"&lt;script&gt;Stress by segment&lt;/script&gt;" in preview.data
    assert b"&lt;script&gt;Which segment is highest?&lt;/script&gt;" in preview.data
    assert b"<script>Stress by segment</script>" not in preview.data
    token = preview_url.rsplit("/", 1)[-1]
    chart = client.get(f"{preview_url}/chart")
    assert chart.status_code == 200
    assert chart.data.startswith(b"\x89PNG\r\n\x1a\n")

    saved_post = client.post(
        f"/visualizations/{dataset_id}/preview/{token}/save"
    )
    assert saved_post.status_code == 303
    saved_url = saved_post.headers["Location"]
    match = re.search(r"/(VIS-[0-9A-F]{16})$", saved_url)
    assert match is not None
    visualization_id = match.group(1)
    saved = client.get(saved_url)
    assert saved.status_code == 200
    assert b"Include in final report" in saved.data
    assert b">Yes<" in saved.data
    saved_path = (
        Path(app.config["VISUALIZATION_DIR"])
        / dataset_id
        / f"{visualization_id}.json"
    )
    payload = json.loads(saved_path.read_text(encoding="utf-8"))
    old_chart = Path(app.config["CHART_DIR"]) / payload["chart"]["filename"]
    assert payload["classification"] == "supplementary"
    assert payload["schema_version"] == 3
    assert payload["spec"]["purpose"] == (
        "<script>Which segment is highest?</script>"
    )
    assert old_chart.is_file()

    regenerated = client.post(
        f"/visualizations/{dataset_id}/{visualization_id}/regenerate"
    )
    assert regenerated.status_code == 303
    updated = json.loads(saved_path.read_text(encoding="utf-8"))
    assert updated["visualization_id"] == visualization_id
    assert updated["chart"]["filename"] != payload["chart"]["filename"]
    assert not old_chart.exists()

    edit = client.get(
        f"/visualizations/{dataset_id}/new?edit={visualization_id}"
    )
    assert edit.status_code == 200
    assert b'value="VIS-' in edit.data

    replacement_preview = client.post(
        f"/visualizations/{dataset_id}/preview",
        data={
            "title": "Updated stress chart",
            "purpose": "Compare the updated segment values.",
            "chart_type": "category_bar_horizontal",
            "measure_selectors": ["column:stress"],
            "x_column": "segment",
            "series_column": "",
            "aggregation": "mean",
            "date_granularity": "month",
            "filter_column": "",
            "filter_mode": "include",
            "filter_values": "",
            "date_start": "",
            "date_end": "",
            "sort_by": "label",
            "sort_direction": "ascending",
            "top_n": "10",
            "scale": "linear",
            "bin_count": "10",
            "include_in_report": "yes",
            "replaces_visualization_id": visualization_id,
        },
    )
    assert replacement_preview.status_code == 303
    replacement_token = replacement_preview.headers["Location"].rsplit("/", 1)[-1]
    replaced = client.post(
        f"/visualizations/{dataset_id}/preview/{replacement_token}/save"
    )
    assert replaced.status_code == 303
    assert replaced.headers["Location"].endswith(visualization_id)
    replacement_payload = json.loads(saved_path.read_text(encoding="utf-8"))
    assert replacement_payload["spec"]["title"] == "Updated stress chart"
    assert replacement_payload["visualization_id"] == visualization_id

    listing = client.get(f"/visualizations/{dataset_id}")
    assert listing.status_code == 200
    assert b"supplementary" in listing.data
    assert client.get(f"/visualizations/{dataset_id}/not-valid").status_code == 404
