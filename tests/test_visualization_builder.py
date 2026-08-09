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
from insight_reporter.visualization_suggestions import VisualizationSuggestion


def _dataset(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "dataset.csv"
    path.write_text(
        (
            "date,segment,region,revenue,cost,stress,net,note\n"
            "2026-01-01,A,North,100,60,2,-5,alpha\n"
            "2026-01-02,B,South,200,120,4,0,beta\n"
            "2026-02-01,A,South,150,80,6,5,gamma\n"
            "2026-02-02,B,North,250,140,8,10,delta\n"
            "2026-03-01,A,North,180,90,10,15,epsilon\n"
            "2026-03-02,B,South,300,160,6,20,zeta\n"
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
            measure_selectors=[f"metric:{configuration.primary_metric_id}"],
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


def test_extended_management_chart_styles_render_valid_pngs(
    tmp_path: Path,
) -> None:
    _path, view, profile, configuration = _dataset(tmp_path)
    revenue_selector = f"metric:{configuration.primary_metric_id}"
    cost_selector = next(
        f"metric:{metric.metric_id}" for metric in configuration.metrics if metric.name == "cost"
    )
    cases = (
        _spec(
            title="Stress area",
            chart_type="time_area",
            x_column="date",
        ),
        _spec(
            title="Revenue mix over time",
            chart_type="time_area_stacked",
            measure_selectors=[revenue_selector],
            x_column="date",
            series_column="segment",
            aggregation="sum",
        ),
        _spec(
            title="Revenue composition",
            chart_type="category_bar_stacked",
            measure_selectors=[revenue_selector],
            series_column="region",
            aggregation="sum",
        ),
        _spec(
            title="Revenue Pareto",
            chart_type="pareto",
            measure_selectors=[revenue_selector],
            aggregation="sum",
        ),
        _spec(
            title="Revenue share",
            chart_type="donut",
            measure_selectors=[revenue_selector],
            aggregation="sum",
        ),
        _spec(
            title="Stress heatmap",
            chart_type="heatmap",
            series_column="region",
        ),
        _spec(
            title="Net waterfall",
            chart_type="waterfall",
            measure_selectors=["column:net"],
            aggregation="sum",
        ),
        _spec(
            title="Revenue funnel",
            chart_type="funnel",
            measure_selectors=[revenue_selector],
            aggregation="sum",
        ),
        _spec(
            title="Revenue and cost",
            chart_type="combo",
            measure_selectors=[revenue_selector, cost_selector],
            aggregation="sum",
        ),
        _spec(
            title="Total revenue",
            chart_type="scorecard",
            measure_selectors=[revenue_selector],
            x_column="",
            aggregation="sum",
        ),
    )

    for spec in cases:
        artifact = build_visualization(
            view,
            profile=profile,
            configuration=configuration,
            spec=spec,
            chart_dir=tmp_path / "charts",
        )
        chart_path = tmp_path / "charts" / artifact.chart.filename
        assert artifact.chart.chart_type == spec.chart_type
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
        (row["measure"], row["x"]): row["value"] for row in multiple.supporting_data
    }
    assert by_measure_and_segment[("revenue", "A")] == 430
    assert by_measure_and_segment[("cost", "B")] == 420
    ratio_by_segment = {row["x"]: row["value"] for row in ratio.supporting_data}
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
        metric for metric in mixed_configuration.metrics if metric.name == "Revenue cost ratio"
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


def _upload_visualization_dataset(client: FlaskClient) -> str:
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
    return match.group(1).decode("ascii")


def _upload_and_configure(client: FlaskClient) -> str:
    dataset_id = _upload_visualization_dataset(client)
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


def test_dashboard_visualization_can_precede_kpis_and_survives_configuration(
    app: Flask,
    client: FlaskClient,
) -> None:
    dataset_id = _upload_visualization_dataset(client)

    dashboard = client.get(f"/workspaces/{dataset_id}/dashboard")
    workspace = client.get(f"/workspaces/{dataset_id}")
    builder = client.get(f"/visualizations/{dataset_id}/new")
    assert dashboard.status_code == 200
    assert workspace.status_code == 200
    assert b"<h1>Dashboard</h1>" in dashboard.data
    assert b"No KPIs are configured yet" in dashboard.data
    assert b"Your dashboard is empty" in dashboard.data
    assert b"Automated Visualization" in dashboard.data
    assert b"Build Manual Visualization" in dashboard.data
    assert f'href="/visualizations/{dataset_id}/new"'.encode("ascii") in dashboard.data
    assert (
        f'href="/visualizations/{dataset_id}/manual/new"'.encode("ascii")
        in dashboard.data
    )
    assert (
        f'href="/visualizations/{dataset_id}/build"'.encode("ascii")
        in workspace.data
    )
    chooser = client.get(f"/visualizations/{dataset_id}/build")
    assert chooser.status_code == 200
    assert b"Choose how you want to create the chart" in chooser.data
    assert b"Automated Visualization" in chooser.data
    assert b"Build Manual Visualization" in chooser.data
    assert f'href="/visualizations/{dataset_id}/new"'.encode("ascii") in chooser.data
    assert (
        f'href="/visualizations/{dataset_id}/manual/new"'.encode("ascii")
        in chooser.data
    )
    assert builder.status_code == 200
    assert builder.headers["Content-Security-Policy"] == "default-src 'self'"
    assert b"Start with the business question" in builder.data
    manual_builder = client.get(f"/visualizations/{dataset_id}/manual/new")
    assert manual_builder.status_code == 200
    assert manual_builder.headers["Content-Security-Policy"] == (
        "default-src 'self'; img-src 'self' blob: data:"
    )
    assert b"<h1>Build Manual Visualization</h1>" in manual_builder.data
    assert b"manual_visualization_builder.js" in manual_builder.data
    assert b"Drag fields here to create a visualization" in manual_builder.data
    assert b"X-axis fields" in manual_builder.data
    assert b"Measures" in manual_builder.data
    assert b'data-field-group="x"' in manual_builder.data
    assert b'data-field-group="y"' in manual_builder.data
    assert b'data-field-name="segment"' in manual_builder.data
    assert b'data-field-name="revenue"' in manual_builder.data
    assert manual_builder.data.count(b'data-field-name="segment"') == 1
    assert manual_builder.data.count(b'data-field-name="revenue"') == 2
    assert manual_builder.data.count(b'data-field-name="date"') == 1
    assert re.search(
        rb'data-field-name="segment"\s+data-field-kind="categorical"\s+'
        rb'data-preferred-axis="x"',
        manual_builder.data,
    )
    assert re.search(
        rb'data-field-name="revenue"\s+data-field-kind="numeric"\s+'
        rb'data-preferred-axis="y"',
        manual_builder.data,
    )
    assert b'data-chart-type="auto"' in manual_builder.data
    assert b'data-chart-type="scatter"' in manual_builder.data
    assert b'data-chart-type="stacked_column"' in manual_builder.data
    assert b'data-chart-type="stacked_bar"' in manual_builder.data
    assert b'data-chart-type="pie"' in manual_builder.data
    assert b'data-chart-type="card"' in manual_builder.data
    assert b'data-chart-type="table"' in manual_builder.data
    assert b'data-chart-type="pareto"' in manual_builder.data
    assert b'data-chart-type="waterfall"' in manual_builder.data
    assert b'data-chart-type="funnel"' in manual_builder.data
    assert b'data-chart-type="treemap"' in manual_builder.data
    assert b'data-chart-type="box"' in manual_builder.data
    assert b'data-chart-type="heatmap"' in manual_builder.data
    assert b'data-chart-type="grouped_column"' in manual_builder.data
    assert b'data-chart-type="bubble"' in manual_builder.data
    assert b'data-chart-type="multi_line"' in manual_builder.data
    assert b'data-chart-type="combo"' in manual_builder.data
    assert b'data-chart-type="stacked_100_column"' in manual_builder.data
    assert b'data-chart-type="stacked_100_bar"' in manual_builder.data
    assert b'data-chart-type="radar"' in manual_builder.data
    assert b'data-chart-type="gauge"' in manual_builder.data
    assert b'data-chart-type="bullet"' in manual_builder.data
    chart_script = client.get("/static/manual_visualization_builder.js")
    assert chart_script.status_code == 200
    assert b"legendFieldSelect" in chart_script.data
    assert b"updateOptionalField" in chart_script.data
    assert b"rasterizePreview" in chart_script.data
    assert b'toDataURL("image/png")' in chart_script.data
    assert b'preview.removeAttribute("hidden")' in chart_script.data
    assert b'preview.hasAttribute("hidden")' in chart_script.data
    assert b'data-drop-zone="x"' in manual_builder.data
    assert b'data-drop-zone="y"' in manual_builder.data
    assert b'data-drop-zone="series"' in manual_builder.data
    assert b'data-drop-zone="size"' in manual_builder.data
    assert b'data-drop-zone="secondary_y"' in manual_builder.data
    assert b'id="pareto-line-mode"' in manual_builder.data
    assert b'value="individual_percent"' in manual_builder.data
    assert b'id="target-value"' in manual_builder.data
    assert b"Let Ollama recommend a chart" not in manual_builder.data
    assert b"KPIs will appear here after they are configured" in builder.data
    assert (
        re.search(
            rb'value="column:revenue"\s+checked',
            builder.data,
        )
        is not None
    )
    assert (
        re.search(
            rb'value="category_bar"\s+checked',
            builder.data,
        )
        is not None
    )
    assert b"Advanced options" in builder.data

    preview_post = client.post(
        f"/visualizations/{dataset_id}/preview",
        data={
            "title": "Stress by segment before KPIs",
            "purpose": "Compare source values before KPI configuration.",
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
    token = preview_post.headers["Location"].rsplit("/", 1)[-1]
    saved = client.post(f"/visualizations/{dataset_id}/preview/{token}/save")
    assert saved.status_code == 303
    visualization_id = saved.headers["Location"].rsplit("/", 1)[-1]
    saved_path = Path(app.config["VISUALIZATION_DIR"]) / dataset_id / f"{visualization_id}.json"
    assert saved_path.is_file()

    configured = client.post(
        f"/configure/{dataset_id}",
        data={
            "primary_kpi": "revenue",
            "kpi_direction": "higher",
            "date_column": "date",
            "category_columns": ["segment"],
            "target_or_benchmark": "",
            "business_objective": "Review revenue and saved visualizations.",
        },
    )
    assert configured.status_code == 303

    reopened_dashboard = client.get(f"/workspaces/{dataset_id}/dashboard")
    reopened_builder = client.get(f"/visualizations/{dataset_id}/new")
    saved_visualization = client.get(f"/visualizations/{dataset_id}/{visualization_id}")
    assert reopened_dashboard.status_code == 200
    assert b"Stress by segment before KPIs" in reopened_dashboard.data
    assert b"1 configured KPI(s)" in reopened_dashboard.data
    assert b'class="visualization-card"' in reopened_dashboard.data
    chart_url = f"/visualizations/{dataset_id}/{visualization_id}/chart"
    assert f'src="{chart_url}"'.encode("ascii") in reopened_dashboard.data
    assert b'loading="lazy"' in reopened_dashboard.data
    dashboard_chart = client.get(chart_url)
    assert dashboard_chart.status_code == 200
    assert dashboard_chart.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert reopened_builder.status_code == 200
    assert b'revenue <span class="tag">KPI</span>' in reopened_builder.data
    assert saved_visualization.status_code == 200

    evidence = client.post(f"/insights/{dataset_id}")
    report_setup = client.get(f"/reports/{dataset_id}/configure")
    assert evidence.status_code == 303
    assert report_setup.status_code == 200
    assert b"Stress by segment before KPIs" in report_setup.data
    assert visualization_id.encode("ascii") in report_setup.data


def test_manual_visualization_board_returns_bounded_live_preview_data(
    client: FlaskClient,
) -> None:
    dataset_id = _upload_visualization_dataset(client)
    endpoint = f"/visualizations/{dataset_id}/manual/preview-data"

    grouped = client.get(
        endpoint,
        query_string={"chart": "auto", "x": "segment", "y": "revenue"},
    )
    assert grouped.status_code == 200
    grouped_payload = grouped.get_json()
    assert grouped_payload["chart_type"] == "column"
    assert grouped_payload["aggregation"] == "Sum of revenue"
    assert grouped_payload["record_count"] == 6
    assert grouped_payload["points"] == [
        {"x": "B", "y": 750.0},
        {"x": "A", "y": 430.0},
    ]

    time_series = client.get(
        endpoint,
        query_string={"chart": "auto", "x": "date", "y": "revenue"},
    )
    assert time_series.status_code == 200
    assert time_series.get_json()["chart_type"] == "line"

    scatter = client.get(
        endpoint,
        query_string={"chart": "auto", "x": "stress", "y": "revenue"},
    )
    assert scatter.status_code == 200
    assert scatter.get_json()["chart_type"] == "scatter"
    assert len(scatter.get_json()["points"]) == 6

    histogram = client.get(
        endpoint,
        query_string={"chart": "auto", "y": "revenue"},
    )
    assert histogram.status_code == 200
    assert histogram.get_json()["chart_type"] == "histogram"
    assert sum(point["y"] for point in histogram.get_json()["points"]) == 6

    stacked = client.get(
        endpoint,
        query_string={
            "chart": "auto",
            "x": "date",
            "y": "revenue",
            "series": "segment",
        },
    )
    assert stacked.status_code == 200
    stacked_payload = stacked.get_json()
    assert stacked_payload["chart_type"] == "stacked_column"
    assert stacked_payload["series_label"] == "segment"
    assert stacked_payload["record_count"] == 6
    assert {point["series"] for point in stacked_payload["points"]} == {"A", "B"}

    grouped_columns = client.get(
        endpoint,
        query_string={
            "chart": "grouped_column",
            "x": "date",
            "y": "revenue",
            "series": "segment",
        },
    )
    assert grouped_columns.status_code == 200
    assert grouped_columns.get_json()["chart_type"] == "grouped_column"
    assert grouped_columns.get_json()["series_label"] == "segment"

    for chart_type in (
        "multi_line",
        "stacked_100_column",
        "stacked_100_bar",
    ):
        series_chart = client.get(
            endpoint,
            query_string={
                "chart": chart_type,
                "x": "date",
                "y": "revenue",
                "series": "segment",
            },
        )
        assert series_chart.status_code == 200
        assert series_chart.get_json()["chart_type"] == chart_type

    combo = client.get(
        endpoint,
        query_string={
            "chart": "combo",
            "x": "date",
            "y": "revenue",
            "secondary_y": "stress",
        },
    )
    assert combo.status_code == 200
    combo_payload = combo.get_json()
    assert combo_payload["secondary_y_label"] == "stress"
    assert combo_payload["points"][0] == {
        "x": "2026-01-01",
        "y": 100.0,
        "secondary_y": 2.0,
    }

    bubble = client.get(
        endpoint,
        query_string={
            "chart": "auto",
            "x": "stress",
            "y": "revenue",
            "size": "stress",
        },
    )
    assert bubble.status_code == 200
    bubble_payload = bubble.get_json()
    assert bubble_payload["chart_type"] == "bubble"
    assert bubble_payload["size_label"] == "stress"
    assert bubble_payload["points"][0] == {"x": 2.0, "y": 100.0, "size": 2.0}

    pie = client.get(
        endpoint,
        query_string={"chart": "pie", "x": "segment", "y": "revenue"},
    )
    assert pie.status_code == 200
    assert pie.get_json()["chart_type"] == "pie"
    assert pie.get_json()["points"][0] == {"x": "B", "y": 750.0}

    card = client.get(
        endpoint,
        query_string={"chart": "card", "y": "revenue"},
    )
    assert card.status_code == 200
    assert card.get_json()["points"] == [{"x": "revenue", "y": 1180.0}]

    table = client.get(
        endpoint,
        query_string={"chart": "table", "x": "segment", "y": "revenue"},
    )
    assert table.status_code == 200
    assert table.get_json()["chart_type"] == "table"

    for chart_type in ("pareto", "waterfall", "funnel", "treemap"):
        response = client.get(
            endpoint,
            query_string={"chart": chart_type, "x": "segment", "y": "revenue"},
        )
        assert response.status_code == 200
        assert response.get_json()["chart_type"] == chart_type

    box = client.get(
        endpoint,
        query_string={"chart": "box", "x": "segment", "y": "revenue"},
    )
    assert box.status_code == 200
    box_payload = box.get_json()
    assert box_payload["chart_type"] == "box"
    assert box_payload["points"][0] == {
        "x": "A",
        "minimum": 100.0,
        "q1": 125.0,
        "median": 150.0,
        "q3": 165.0,
        "maximum": 180.0,
        "count": 3,
    }

    heatmap = client.get(
        endpoint,
        query_string={
            "chart": "heatmap",
            "x": "date",
            "y": "revenue",
            "series": "segment",
        },
    )
    assert heatmap.status_code == 200
    assert heatmap.get_json()["chart_type"] == "heatmap"
    assert heatmap.get_json()["series_label"] == "segment"

    radar = client.get(
        endpoint,
        query_string={"chart": "radar", "x": "date", "y": "revenue"},
    )
    assert radar.status_code == 200
    assert radar.get_json()["chart_type"] == "radar"
    assert len(radar.get_json()["points"]) == 6

    gauge = client.get(
        endpoint,
        query_string={"chart": "gauge", "y": "revenue", "target": "1000"},
    )
    assert gauge.status_code == 200
    assert gauge.get_json()["target"] == 1000.0
    assert gauge.get_json()["points"] == [{"x": "revenue", "y": 1180.0}]

    bullet = client.get(
        endpoint,
        query_string={"chart": "bullet", "y": "revenue", "target": "1500"},
    )
    assert bullet.status_code == 200
    assert bullet.get_json()["chart_type"] == "bullet"

    missing_target = client.get(
        endpoint,
        query_string={"chart": "gauge", "y": "revenue"},
    )
    assert missing_target.status_code == 422
    assert b"Enter a positive target value" in missing_target.data

    invalid = client.get(
        endpoint,
        query_string={"chart": "line", "x": "segment", "y": "revenue"},
    )
    assert invalid.status_code == 422
    assert b"need a date/time X-axis" in invalid.data

    unknown_field = client.get(
        endpoint,
        query_string={"chart": "auto", "x": "not-a-column"},
    )
    assert unknown_field.status_code == 422
    assert b"selected X-axis field is unavailable" in unknown_field.data


def test_manual_visualization_can_be_saved_reopened_updated_and_shown_on_dashboard(
    app: Flask,
    client: FlaskClient,
) -> None:
    dataset_id = _upload_visualization_dataset(client)
    save_url = f"/visualizations/{dataset_id}/manual/save"
    svg = (
        '<svg viewBox="0 0 800 460" role="img">'
        "<title>Revenue by segment</title>"
        '<rect x="10" y="10" width="100" height="50" fill="#2563eb"/>'
        "</svg>"
    )
    payload = {
        "visualization_id": None,
        "title": "Revenue by segment",
        "chart": "column",
        "fields": {
            "x": "segment",
            "y": "revenue",
            "series": None,
            "size": None,
            "secondary_y": None,
        },
        "settings": {
            "pareto_line": "cumulative_percent",
            "target": None,
        },
        "svg": svg,
    }

    saved = client.post(save_url, json=payload)
    assert saved.status_code == 201
    saved_payload = saved.get_json()
    visualization_id = saved_payload["visualization_id"]
    assert re.fullmatch(r"MBV-[0-9A-F]{16}", visualization_id)
    artifact_path = (
        Path(app.config["VISUALIZATION_DIR"])
        / "manual_boards"
        / dataset_id
        / f"{visualization_id}.json"
    )
    assert artifact_path.is_file()
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["chart_type"] == "column"
    assert artifact["fields"]["x"] == "segment"

    detail = client.get(saved_payload["url"])
    chart = client.get(f"{saved_payload['url']}/chart")
    dashboard = client.get(f"/workspaces/{dataset_id}/dashboard")
    edit = client.get(f"/visualizations/{dataset_id}/manual/new?edit={visualization_id}")
    assert detail.status_code == 200
    assert b"Revenue by segment" in detail.data
    assert b"Edit visualization" in detail.data
    assert chart.status_code == 200
    assert chart.mimetype == "image/svg+xml"
    assert b"<script" not in chart.data
    assert chart.headers["Content-Security-Policy"] == "sandbox; default-src 'none'"
    assert dashboard.status_code == 200
    assert b"Manual board" in dashboard.data
    assert b"Revenue by segment" in dashboard.data
    assert (
        f"src=\"{saved_payload['url']}/chart\"".encode("ascii")
        in dashboard.data
    )
    assert client.get(f"{saved_payload['url']}/chart.png").status_code == 404
    assert edit.status_code == 200
    assert visualization_id.encode("ascii") in edit.data
    assert b'"chart": "column"' in edit.data

    payload["visualization_id"] = visualization_id
    payload["title"] = "Updated revenue by segment"
    updated = client.post(save_url, json=payload)
    assert updated.status_code == 200
    updated_artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert updated_artifact["visualization_id"] == visualization_id
    assert updated_artifact["title"] == "Updated revenue by segment"
    assert updated_artifact["created_at"] == artifact["created_at"]

    unsafe_payload = dict(payload)
    unsafe_payload["visualization_id"] = None
    unsafe_payload["svg"] = '<svg viewBox="0 0 800 460"><script>alert(1)</script></svg>'
    rejected = client.post(save_url, json=unsafe_payload)
    assert rejected.status_code == 422
    assert b"unsafe element" in rejected.data

    invalid_png_payload = dict(payload)
    invalid_png_payload["visualization_id"] = None
    invalid_png_payload["png"] = "data:image/png;base64,bm90LWEtcG5n"
    invalid_png = client.post(save_url, json=invalid_png_payload)
    assert invalid_png.status_code == 422
    assert b"PNG" in invalid_png.data


def test_ollama_chart_suggestion_builds_a_reviewable_validated_preview(
    app: Flask,
    client: FlaskClient,
    monkeypatch,
) -> None:
    dataset_id = _upload_and_configure(client)
    builder = client.get(f"/visualizations/{dataset_id}/new")
    selector_match = re.search(
        rb'name="measure_selectors" value="(metric:[^"]+)"',
        builder.data,
    )
    assert selector_match is not None
    selector = selector_match.group(1).decode("ascii")
    captured: dict[str, object] = {}

    def suggest(*_args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return VisualizationSuggestion(
            spec=_spec(
                title="Revenue contribution by segment",
                purpose="Compare which segment contributes the most revenue.",
                chart_type="donut",
                measure_selectors=[selector],
                x_column="segment",
                aggregation="sum",
                top_n="7",
            ),
            confidence=0.89,
            rationale=(
                "Segment is a detected low-cardinality category.",
                "Revenue is a configured summable KPI.",
            ),
            user_request="Show revenue share by segment.",
        )

    monkeypatch.setattr(
        "insight_reporter.routes.generate_visualization_suggestion",
        suggest,
    )

    response = client.post(
        f"/visualizations/{dataset_id}/assistant",
        data={"user_request": "Show revenue share by segment."},
    )

    assert response.status_code == 303
    assert captured["user_request"] == "Show revenue share by segment."
    preview = client.get(response.headers["Location"])
    assert preview.status_code == 200
    assert b"Ollama-assisted setup" in preview.data
    assert b"Show revenue share by segment." in preview.data
    assert b"Revenue contribution by segment" in preview.data
    token = response.headers["Location"].rsplit("/", 1)[-1]
    saved = client.post(f"/visualizations/{dataset_id}/preview/{token}/save")
    assert saved.status_code == 303
    visualization_id = saved.headers["Location"].rsplit("/", 1)[-1]
    payload = json.loads(
        (Path(app.config["VISUALIZATION_DIR"]) / dataset_id / f"{visualization_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["assistant"]["method"] == "ollama_assisted"
    assert payload["assistant"]["confidence"] == 0.89
    reopened = client.get(saved.headers["Location"])
    assert b"Ollama-assisted setup" in reopened.data


def test_saved_chart_insight_route_persists_findings_and_report_preference(
    app: Flask,
    client: FlaskClient,
) -> None:
    dataset_id = _upload_and_configure(client)
    builder = client.get(f"/visualizations/{dataset_id}/new")
    selector_match = re.search(
        rb'name="measure_selectors" value="(metric:[^"]+)"',
        builder.data,
    )
    assert selector_match is not None
    preview = client.post(
        f"/visualizations/{dataset_id}/preview",
        data={
            "title": "Revenue by segment",
            "purpose": "Where should management focus?",
            "chart_type": "category_bar",
            "measure_selectors": [selector_match.group(1).decode("ascii")],
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
        },
    )
    token = preview.headers["Location"].rsplit("/", 1)[-1]
    saved = client.post(f"/visualizations/{dataset_id}/preview/{token}/save")
    visualization_id = saved.headers["Location"].rsplit("/", 1)[-1]

    detail_before_insight = client.get(saved.headers["Location"])
    assert b"Verified observations" in detail_before_insight.data
    assert b"Suggest questions with Ollama" not in detail_before_insight.data
    assert b"Ask about this visualization" not in detail_before_insight.data
    assert b"visualization_insights.js" not in detail_before_insight.data

    generated = client.post(
        f"/visualizations/{dataset_id}/{visualization_id}/insights/report-inclusion",
        data={"include_in_reports": "yes"},
    )
    assert generated.status_code == 303
    detail = client.get(generated.headers["Location"])
    assert detail.status_code == 200
    assert b"Verified observations" in detail.data
    assert b"Observation 1" in detail.data
    assert b"Included when this visualization is selected" in detail.data
    assert b"Verified observations will be included" in detail.data

    insight_path = (
        Path(app.config["VISUALIZATION_INSIGHT_DIR"]) / dataset_id / f"{visualization_id}.json"
    )
    payload = json.loads(insight_path.read_text(encoding="utf-8"))
    assert payload["include_in_reports"] is True
    assert payload["model_status"] == "not_requested"
    assert payload["answers"] == []
    assert len(payload["facts"]) == 5

    excluded = client.post(
        (f"/visualizations/{dataset_id}/{visualization_id}/insights/report-inclusion"),
        data={},
    )
    assert excluded.status_code == 303
    updated = json.loads(insight_path.read_text(encoding="utf-8"))
    assert updated["include_in_reports"] is False


def test_supplementary_preview_save_reopen_regenerate_and_edit_workflow(
    app: Flask,
    client: FlaskClient,
) -> None:
    dataset_id = _upload_and_configure(client)
    builder = client.get(f"/visualizations/{dataset_id}/new")
    assert builder.status_code == 200
    assert b"Create a visualization" in builder.data
    assert b"1. What do you want to understand?" in builder.data
    assert b"2. Which number should the chart explain?" in builder.data
    assert b"3. How should the results be organized?" in builder.data
    assert b"4. Name the chart" in builder.data
    assert b"What decision or question will this chart support?" in builder.data
    assert b'value="column:stress"' in builder.data
    assert (
        re.search(
            rb'value="metric:[^"]+"\s+checked',
            builder.data,
        )
        is not None
    )
    assert b"visualization_builder.js" in builder.data
    assert b"Describe what you want to visualize" not in builder.data
    assert b"Generate validated preview with Ollama" not in builder.data
    assert b"Let Ollama recommend a chart" in builder.data
    assert b"Suggest and preview a chart" in builder.data

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

    saved_post = client.post(f"/visualizations/{dataset_id}/preview/{token}/save")
    assert saved_post.status_code == 303
    saved_url = saved_post.headers["Location"]
    match = re.search(r"/(VIS-[0-9A-F]{16})$", saved_url)
    assert match is not None
    visualization_id = match.group(1)
    saved = client.get(saved_url)
    assert saved.status_code == 200
    assert b"Include in final report" in saved.data
    assert b">Yes<" in saved.data
    saved_path = Path(app.config["VISUALIZATION_DIR"]) / dataset_id / f"{visualization_id}.json"
    payload = json.loads(saved_path.read_text(encoding="utf-8"))
    old_chart = Path(app.config["CHART_DIR"]) / payload["chart"]["filename"]
    assert payload["classification"] == "supplementary"
    assert payload["schema_version"] == 3
    assert payload["spec"]["purpose"] == ("<script>Which segment is highest?</script>")
    assert old_chart.is_file()

    regenerated = client.post(f"/visualizations/{dataset_id}/{visualization_id}/regenerate")
    assert regenerated.status_code == 303
    updated = json.loads(saved_path.read_text(encoding="utf-8"))
    assert updated["visualization_id"] == visualization_id
    assert updated["chart"]["filename"] != payload["chart"]["filename"]
    assert not old_chart.exists()

    edit = client.get(f"/visualizations/{dataset_id}/new?edit={visualization_id}")
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
    replaced = client.post(f"/visualizations/{dataset_id}/preview/{replacement_token}/save")
    assert replaced.status_code == 303
    assert replaced.headers["Location"].endswith(visualization_id)
    replacement_payload = json.loads(saved_path.read_text(encoding="utf-8"))
    assert replacement_payload["spec"]["title"] == "Updated stress chart"
    assert replacement_payload["visualization_id"] == visualization_id

    listing = client.get(f"/visualizations/{dataset_id}")
    assert listing.status_code == 200
    assert b"supplementary" in listing.data
    assert client.get(f"/visualizations/{dataset_id}/not-valid").status_code == 404
