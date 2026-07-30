"""Conditional count-rate and value-share KPI tests."""

import io
import json
import re
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from insight_reporter.business_config import (
    load_business_configuration,
    save_business_configuration,
    validate_business_configuration,
    validate_conditional_business_configuration,
)
from insight_reporter.conditional_metrics import (
    ConditionalMetricError,
    condition_value_options,
    evaluate_conditional_metric,
    load_conditional_metric,
    validate_conditional_metric,
)
from insight_reporter.dataset_profile import profile_csv
from insight_reporter.dataset_view import CsvDatasetView, source_id_from_hash
from insight_reporter.insight_engine import generate_insights

_CSV = (
    "date,region,customer_type,status,net_sales\n"
    "2026-01-01,North,New,Completed,100\n"
    "2026-01-02,South,Returning,Returned,200\n"
    "2026-01-03,North,New,Cancelled,50\n"
    "2026-02-01,South,Returning,Completed,150\n"
    "2026-02-02,North,Returning,Completed,100\n"
    "2026-02-03,South,New,Completed,100\n"
)


def _inputs(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "conditional.csv"
    path.write_text(_CSV, encoding="utf-8")
    return path, CsvDatasetView.from_path(path), profile_csv(path)


def test_record_count_rate_is_validated_and_calculated(tmp_path: Path) -> None:
    _path, view, profile = _inputs(tmp_path)
    metric = validate_conditional_metric(
        profile,
        view,
        name="Return/Cancellation Rate",
        calculation_base="record_count",
        condition_column="status",
        included_values=["Returned", "Cancelled"],
        value_column="",
        row_grain_confirmed=True,
        source_id=source_id_from_hash(profile.source_sha256),
    )

    result = evaluate_conditional_metric(
        metric,
        tuple(dict(row.values) for row in view.iter_rows()),
    )

    assert metric.formula_label.startswith("COUNT(rows where [status]")
    assert result.numerator == 2
    assert result.denominator == 6
    assert result.percentage == pytest.approx(33.3333333333)
    assert result.numerator_record_count == 2
    assert result.denominator_record_count == 6


def test_value_share_and_category_options_use_exact_values(
    tmp_path: Path,
) -> None:
    _path, view, profile = _inputs(tmp_path)
    options = condition_value_options(view, profile)
    metric = validate_conditional_metric(
        profile,
        view,
        name="New Customer Revenue Share",
        calculation_base="value_sum",
        condition_column="customer_type",
        included_values=["New"],
        value_column="net_sales",
        row_grain_confirmed=False,
        source_id=source_id_from_hash(profile.source_sha256),
    )

    result = evaluate_conditional_metric(
        metric,
        tuple(dict(row.values) for row in view.iter_rows()),
    )

    assert options["customer_type"] == ("New", "Returning")
    assert result.numerator == 250
    assert result.denominator == 700
    assert result.percentage == pytest.approx(35.7142857143)


def test_record_rate_requires_row_grain_and_real_values(
    tmp_path: Path,
) -> None:
    _path, view, profile = _inputs(tmp_path)
    arguments = {
        "profile": profile,
        "view": view,
        "name": "Problem Rate",
        "calculation_base": "record_count",
        "condition_column": "status",
        "included_values": ["Returned"],
        "value_column": "",
        "row_grain_confirmed": False,
        "source_id": source_id_from_hash(profile.source_sha256),
    }
    with pytest.raises(ConditionalMetricError, match="one dataset row"):
        validate_conditional_metric(**arguments)  # type: ignore[arg-type]
    arguments["row_grain_confirmed"] = True
    arguments["included_values"] = ["Invented"]
    with pytest.raises(ConditionalMetricError, match="do not exist"):
        validate_conditional_metric(**arguments)  # type: ignore[arg-type]


def test_conditional_configuration_round_trips_and_generates_grouped_insights(
    tmp_path: Path,
) -> None:
    path, view, profile = _inputs(tmp_path)
    source_id = source_id_from_hash(profile.source_sha256)
    metric = validate_conditional_metric(
        profile,
        view,
        name="Return/Cancellation Rate",
        calculation_base="record_count",
        condition_column="status",
        included_values=["Returned", "Cancelled"],
        value_column="",
        row_grain_confirmed=True,
        source_id=source_id,
    )
    configuration = validate_conditional_business_configuration(
        profile,
        dataset_id="a" * 32,
        conditional_metric=metric,
        kpi_direction="lower",
        date_column="date",
        category_columns=["region"],
        target_or_benchmark="20",
        business_objective="Reduce returns and cancellations.",
        metric_role="primary",
    )
    saved = save_business_configuration(
        configuration,
        configuration_dir=tmp_path / "configurations",
    )

    loaded = load_business_configuration(saved, profile=profile)
    report = generate_insights(
        path,
        profile=profile,
        configuration=loaded,
    )

    assert loaded == configuration
    assert loaded.conditional_metric is not None
    assert loaded.primary_metric.aggregation == "conditional_rate"
    assert loaded.primary_metric.display_format == "percentage"
    period = next(
        insight
        for insight in report.insights
        if insight.type == "period_change"
    )
    assert period.observation["previous_value"] == 66.6666666667
    assert period.observation["current_value"] == 0
    benchmark = next(
        insight
        for insight in report.insights
        if insight.type == "metric_snapshot"
    )
    assert {
        key: benchmark.observation[key]
        for key in (
            "current_value",
            "target",
            "gap_to_target",
            "numerator",
            "denominator",
            "numerator_record_count",
            "denominator_record_count",
            "kpi_direction",
            "meets_target",
            "favorable",
            "target_scope",
        )
    } == {
        "current_value": pytest.approx(33.3333333333),
        "target": 20.0,
        "gap_to_target": pytest.approx(13.3333333333),
        "numerator": 2.0,
        "denominator": 6.0,
        "numerator_record_count": 2,
        "denominator_record_count": 6,
        "kpi_direction": "lower",
        "meets_target": False,
        "favorable": False,
            "target_scope": "dataset",
    }
    ranking = next(
        insight
        for insight in report.insights
        if insight.type == "segment_ranking"
    )
    assert ranking.observation["top_segment"]["segment"] == "North"

    reloaded_definition = load_conditional_metric(
        profile,
        loaded.conditional_metric.to_dict(),
        source_id=source_id,
    )
    assert reloaded_definition == metric


def test_source_kpi_generates_named_reconciled_category_shares(
    tmp_path: Path,
) -> None:
    path, _view, profile = _inputs(tmp_path)
    configuration = validate_business_configuration(
        profile,
        dataset_id="a" * 32,
        primary_kpi="net_sales",
        kpi_direction="higher",
        date_column="date",
        category_columns=["customer_type"],
        target_or_benchmark="",
        business_objective="Track acquisition and retention revenue balance.",
        aggregation="sum",
        display_format="currency",
    )

    report = generate_insights(
        path,
        profile=profile,
        configuration=configuration,
    )
    share = next(
        insight
        for insight in report.insights
        if insight.type == "segment_share"
    )

    shares = {
        item["segment"]: item["share_percentage"]
        for item in share.observation["shares"]
    }
    assert shares["New"] == pytest.approx(35.7142857143)
    assert shares["Returning"] == pytest.approx(64.2857142857)
    assert sum(shares.values()) == pytest.approx(100)
    assert share.observation["total_value"] == 700


def test_conditional_builder_route_saves_exact_selected_values(
    app: Flask,
    client: FlaskClient,
) -> None:
    uploaded = client.post(
        "/upload",
        data={
            "file": (
                io.BytesIO(_CSV.encode()),
                "conditional.csv",
                "text/csv",
            )
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    dataset_id = re.search(
        rb"<dd>([0-9a-f]{32})\.csv</dd>",
        uploaded.data,
    ).group(1).decode()

    editor = client.get(f"/conditional/{dataset_id}")
    assert editor.status_code == 200
    assert b"Build a conditional percentage KPI" in editor.data
    assert b"Returned" in editor.data
    configured = client.post(
        f"/configure-conditional/{dataset_id}",
        data={
            "name": "Return/Cancellation Rate",
            "calculation_base": "record_count",
            "condition_column": "status",
            "included_values::status": ["Returned", "Cancelled"],
            "value_column": "",
            "row_grain_confirmed": "yes",
            "kpi_direction": "lower",
            "target_or_benchmark": "20",
            "target_scope": "segment",
            "metric_role": "primary",
            "date_column": "date",
            "category_columns": ["region"],
            "business_objective": "Reduce returns and cancellations.",
        },
        follow_redirects=True,
    )

    assert configured.status_code == 200
    assert b"Return/Cancellation Rate" in configured.data
    payload = json.loads(
        (
            Path(app.config["CONFIGURATION_DIR"])
            / f"{dataset_id}.json"
        ).read_text(encoding="utf-8")
    )
    definition = payload["metrics"][0]["conditional_metric"]
    assert payload["metrics"][0]["target_scope"] == "segment"
    assert definition["included_values"] == ["Returned", "Cancelled"]
    assert definition["row_grain_confirmed"] is True


def test_conditional_target_must_be_a_percentage(tmp_path: Path) -> None:
    _path, view, profile = _inputs(tmp_path)
    metric = validate_conditional_metric(
        profile,
        view,
        name="Problem Rate",
        calculation_base="record_count",
        condition_column="status",
        included_values=["Returned"],
        value_column="",
        row_grain_confirmed=True,
        source_id=source_id_from_hash(profile.source_sha256),
    )

    with pytest.raises(
        ValueError,
        match="between 0 and 100",
    ):
        validate_conditional_business_configuration(
            profile,
            dataset_id="a" * 32,
            conditional_metric=metric,
            kpi_direction="lower",
            date_column="date",
            category_columns=["region"],
            target_or_benchmark="101",
            business_objective="Reduce problems.",
        )
