"""Acceptance and security tests for JSON and XLSX dataset inputs."""

import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient
from openpyxl import Workbook


def _upload(
    client: FlaskClient,
    content: bytes,
    *,
    filename: str,
    content_type: str = "application/octet-stream",
    follow_redirects: bool = True,
):  # type: ignore[no-untyped-def]
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(content), filename, content_type)},
        content_type="multipart/form-data",
        follow_redirects=follow_redirects,
    )


def _workbook_bytes(
    sheets: dict[str, list[list[object]]],
) -> bytes:
    workbook = Workbook()
    first = True
    for name, rows in sheets.items():
        worksheet = workbook.active if first else workbook.create_sheet()
        first = False
        worksheet.title = name
        for row in rows:
            worksheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _dataset_id(page: bytes) -> str:
    match = re.search(rb"<dd>([0-9a-f]{32})\.(?:csv|json|xlsx)</dd>", page)
    assert match is not None
    return match.group(1).decode("ascii")


def _sheet_dataset_id(page: bytes) -> str:
    match = re.search(rb'action="/dataset/([0-9a-f]{32})/sheet"', page)
    assert match is not None
    return match.group(1).decode("ascii")


def test_flat_json_upload_is_detected_without_trusting_filename(
    app: Flask, client: FlaskClient
) -> None:
    content = json.dumps(
        [
            {"date": "2026-01-01", "region": "North", "revenue": 100},
            {"date": "2026-01-02", "region": "South", "revenue": 120},
        ]
    ).encode()

    response = _upload(
        client,
        content,
        filename="renamed.exe",
        content_type="application/x-msdownload",
    )

    assert response.status_code == 200
    assert b"Dataset accepted" in response.data
    assert b"<dd>JSON</dd>" in response.data
    assert b"North" in response.data
    source_files = list(Path(app.config["UPLOAD_DIR"]).glob("*.json"))
    assert len(source_files) == 1
    assert source_files[0].read_bytes() == content


def test_sparse_json_records_are_normalized_as_missing_values(
    client: FlaskClient,
) -> None:
    content = json.dumps(
        [
            {"region": "North", "revenue": 100},
            {"region": "South"},
        ]
    ).encode()

    response = _upload(client, content, filename="records.json")

    assert response.status_code == 200
    assert b"revenue" in response.data
    assert b"1 (50.00%)" in response.data


def test_json_cell_content_is_html_escaped(client: FlaskClient) -> None:
    response = _upload(
        client,
        b'[{"name":"Alice","note":"<script>alert(1)</script>"}]',
        filename="escaped.json",
    )
    page = response.data.decode("utf-8")

    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page


def test_nested_json_and_duplicate_keys_are_rejected(
    app: Flask, client: FlaskClient
) -> None:
    nested = _upload(
        client,
        b'[{"region":"North","details":{"revenue":100}}]',
        filename="nested.json",
    )
    duplicate = _upload(
        client,
        b'[{"region":"North","region":"South"}]',
        filename="duplicate.json",
    )

    assert nested.status_code == 400
    assert b"nested array or object" in nested.data
    assert duplicate.status_code == 400
    assert b"duplicate key" in duplicate.data
    assert list(Path(app.config["UPLOAD_DIR"]).glob("*")) == []


def test_non_finite_json_numbers_are_rejected(
    app: Flask, client: FlaskClient
) -> None:
    response = _upload(
        client,
        b'[{"value":NaN}]',
        filename="non-finite.json",
    )

    assert response.status_code == 400
    assert b"unsupported numeric constant" in response.data
    assert list(Path(app.config["UPLOAD_DIR"]).glob("*")) == []


def test_json_limits_are_enforced(app: Flask, client: FlaskClient) -> None:
    app.config["MAX_CSV_ROWS"] = 1
    response = _upload(
        client,
        b'[{"value":1},{"value":2}]',
        filename="large.json",
    )

    assert response.status_code == 400
    assert b"maximum of 1 data rows" in response.data


def test_single_sheet_xlsx_preserves_typed_values_and_profiles_automatically(
    app: Flask, client: FlaskClient
) -> None:
    content = _workbook_bytes(
        {
            "Sales Data": [
                ["date", "region", "revenue"],
                [date(2026, 1, 1), "North", 100],
                [date(2026, 1, 2), "South", 120.5],
            ]
        }
    )

    response = _upload(client, content, filename="renamed.csv", content_type="text/csv")

    assert response.status_code == 200
    assert b"<dd>XLSX</dd>" in response.data
    assert b"<dd>Sales Data</dd>" in response.data
    assert b"2026-01-01" in response.data
    dataset_id = _dataset_id(response.data)
    assert (
        Path(app.config["UPLOAD_DIR"]) / f"{dataset_id}.selection.json"
    ).is_file()
    assert (Path(app.config["UPLOAD_DIR"]) / f"{dataset_id}.xlsx").read_bytes() == content


def test_multi_sheet_xlsx_requires_explicit_selection(
    app: Flask, client: FlaskClient
) -> None:
    content = _workbook_bytes(
        {
            "Sales": [["region", "revenue"], ["North", 100], ["South", 120]],
            "Costs": [["region", "cost"], ["North", 60], ["South", 70]],
        }
    )

    selection_page = _upload(client, content, filename="workbook.xlsx")

    assert selection_page.status_code == 200
    assert b"Select an Excel worksheet" in selection_page.data
    assert b"Sales" in selection_page.data
    assert b"Costs" in selection_page.data
    dataset_id = _sheet_dataset_id(selection_page.data)

    profile = client.post(
        f"/dataset/{dataset_id}/sheet",
        data={"table_name": "Costs"},
        follow_redirects=True,
    )

    assert profile.status_code == 200
    assert b"<dd>Costs</dd>" in profile.data
    assert b"cost" in profile.data
    assert b"<td>60</td>" in profile.data

    configured = client.post(
        f"/configure/{dataset_id}",
        data={
            "primary_kpi": "cost",
            "kpi_direction": "lower",
            "date_column": "",
            "category_columns": ["region"],
            "target_or_benchmark": "",
            "business_objective": "Track cost by region.",
        },
        follow_redirects=True,
    )
    configuration = json.loads(
        (
            Path(app.config["CONFIGURATION_DIR"]) / f"{dataset_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert configured.status_code == 200
    assert configuration["sources"][0]["format"] == "xlsx"
    assert configuration["sources"][0]["table_name"] == "Costs"
    insights = client.post(f"/insights/{dataset_id}", follow_redirects=True)
    evidence = json.loads(
        (
            Path(app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert insights.status_code == 200
    assert evidence["sources"][0]["format"] == "xlsx"
    assert evidence["sources"][0]["table_name"] == "Costs"
    assert all(record["source"]["worksheet"] == "Costs" for record in evidence["records"])


def test_unavailable_xlsx_sheet_is_rejected_without_path_injection(
    client: FlaskClient,
) -> None:
    content = _workbook_bytes(
        {
            "Sales": [["region", "revenue"], ["North", 100]],
            "Costs": [["region", "cost"], ["North", 60]],
        }
    )
    selection_page = _upload(client, content, filename="workbook.xlsx")
    dataset_id = _sheet_dataset_id(selection_page.data)

    response = client.post(
        f"/dataset/{dataset_id}/sheet",
        data={"table_name": "../../Sales"},
        follow_redirects=True,
    )

    assert response.status_code == 400
    assert b"Select an available Excel worksheet" in response.data


def test_xlsx_formula_cells_are_rejected(app: Flask, client: FlaskClient) -> None:
    content = _workbook_bytes(
        {
            "Data": [
                ["revenue", "cost", "profit"],
                [100, 60, "=A2-B2"],
            ]
        }
    )

    response = _upload(client, content, filename="formula.xlsx")

    assert response.status_code == 400
    assert b"formula cells are not supported" in response.data
    assert list(Path(app.config["UPLOAD_DIR"]).glob("*")) == []


def test_xlsx_row_and_column_limits_are_enforced(
    app: Flask, client: FlaskClient
) -> None:
    app.config["MAX_CSV_COLUMNS"] = 2
    too_wide = _workbook_bytes(
        {"Data": [["one", "two", "three"], [1, 2, 3]]}
    )

    response = _upload(client, too_wide, filename="wide.xlsx")

    assert response.status_code == 400
    assert b"3 columns" in response.data
    assert b"maximum is 2" in response.data


def test_macro_member_and_legacy_xls_are_rejected(
    app: Flask, client: FlaskClient
) -> None:
    original = _workbook_bytes({"Data": [["value"], [1]]})
    macro_buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source:
        with zipfile.ZipFile(macro_buffer, "w", zipfile.ZIP_DEFLATED) as destination:
            for info in source.infolist():
                destination.writestr(info, source.read(info.filename))
            destination.writestr("xl/vbaProject.bin", b"not-a-real-macro")

    macro = _upload(client, macro_buffer.getvalue(), filename="macro.xlsx")
    legacy = _upload(
        client,
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy-xls",
        filename="legacy.xls",
    )

    assert macro.status_code == 400
    assert b"Macro-enabled" in macro.data
    assert legacy.status_code == 400
    assert b"Binary" in legacy.data
    assert list(Path(app.config["UPLOAD_DIR"]).glob("*")) == []


def test_json_configuration_and_insights_remain_format_independent(
    app: Flask, client: FlaskClient
) -> None:
    records = [
        {
            "date": f"2026-0{month}-{day:02d}",
            "region": region,
            "revenue": value,
        }
        for month, day, region, value in (
            (1, 1, "North", 100),
            (1, 2, "South", 120),
            (2, 1, "North", 140),
            (2, 2, "South", 160),
            (3, 1, "North", 180),
            (3, 2, "South", 200),
        )
    ]
    uploaded = _upload(
        client,
        json.dumps(records).encode(),
        filename="business.json",
    )
    dataset_id = _dataset_id(uploaded.data)

    configured = client.post(
        f"/configure/{dataset_id}",
        data={
            "primary_kpi": "revenue",
            "kpi_direction": "higher",
            "date_column": "date",
            "category_columns": ["region"],
            "target_or_benchmark": "",
            "business_objective": "Track revenue over time.",
        },
        follow_redirects=True,
    )
    insights = client.post(f"/insights/{dataset_id}", follow_redirects=True)

    assert configured.status_code == 200
    assert insights.status_code == 200
    configuration = json.loads(
        (
            Path(app.config["CONFIGURATION_DIR"]) / f"{dataset_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert configuration["schema_version"] == 6
    assert configuration["sources"][0]["format"] == "json"
    assert configuration["sources"][0]["table_name"] is None
    assert b"period_change" in insights.data
    insight_report = json.loads(
        (
            Path(app.config["INSIGHT_DIR"]) / f"{dataset_id}.json"
        ).read_text(encoding="utf-8")
    )
    evidence = json.loads(
        (
            Path(app.config["EVIDENCE_DIR"]) / f"{dataset_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert insight_report["schema_version"] == 4
    assert insight_report["sources"][0]["format"] == "json"
    assert evidence["schema_version"] == 2
    assert all(
        isinstance(record["observation"], dict)
        for record in evidence["records"]
    )
    assert evidence["sources"][0]["format"] == "json"
    assert all(record["source"]["format"] == "json" for record in evidence["records"])
