"""Acceptance and security tests for Milestone 1 CSV ingestion."""

import hashlib
import io
import re
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient


def _upload(
    client: FlaskClient,
    content: bytes,
    *,
    filename: str = "input.csv",
    content_type: str = "text/csv",
):  # type: ignore[no-untyped-def]
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(content), filename, content_type)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def _stored_files(app: Flask) -> list[Path]:
    return sorted(Path(app.config["UPLOAD_DIR"]).glob("*"))


def test_valid_csv_succeeds_and_is_stored_under_random_name(
    app: Flask, client: FlaskClient
) -> None:
    content = b"name,amount\nAlice,10\nBob,20\n"

    response = _upload(
        client,
        content,
        filename="untrusted-name.exe",
        content_type="application/x-msdownload",
    )

    assert response.status_code == 200
    assert b"Dataset accepted" in response.data
    assert b"Alice" in response.data
    assert b"2" in response.data
    stored = _stored_files(app)
    assert len(stored) == 1
    assert re.fullmatch(r"[0-9a-f]{32}\.csv", stored[0].name)
    assert stored[0].read_bytes() == content


def test_utf8_bom_csv_succeeds(client: FlaskClient) -> None:
    response = _upload(client, b"\xef\xbb\xbfcity,total\nPune,42\n")

    assert response.status_code == 200
    assert b"city" in response.data
    assert b"Pune" in response.data


def test_physically_blank_records_are_ignored(client: FlaskClient) -> None:
    response = _upload(client, b"name,amount\nAlice,10\n\n")

    assert response.status_code == 200
    assert b"Alice" in response.data
    assert b"<dd>1</dd>" in response.data


def test_non_utf8_text_is_rejected(app: Flask, client: FlaskClient) -> None:
    response = _upload(client, b"name,note\nAlice,invalid-\xff\n")

    assert response.status_code == 400
    assert b"non-UTF-8 content" in response.data
    assert _stored_files(app) == []


@pytest.mark.parametrize("content", [b"", b"\xef\xbb\xbf", b" \r\n\t"])
def test_empty_csv_is_rejected_and_not_retained(
    app: Flask, client: FlaskClient, content: bytes
) -> None:
    response = _upload(client, content)

    assert response.status_code == 400
    assert b"empty" in response.data.lower()
    assert _stored_files(app) == []


def test_header_only_csv_is_rejected_as_empty_data(app: Flask, client: FlaskClient) -> None:
    response = _upload(client, b"name,amount\n")

    assert response.status_code == 400
    assert b"at least one data row" in response.data
    assert _stored_files(app) == []


def test_renamed_executable_is_rejected(app: Flask, client: FlaskClient) -> None:
    executable_bytes = b"MZ\x90\x00\x03\x00This program cannot be run in DOS mode"

    response = _upload(
        client,
        executable_bytes,
        filename="quarterly-report.csv",
        content_type="text/csv",
    )

    assert response.status_code == 400
    assert b"binary" in response.data.lower()
    assert _stored_files(app) == []


def test_oversized_file_is_rejected_and_incomplete_file_is_deleted(
    app: Flask, client: FlaskClient
) -> None:
    app.config["MAX_UPLOAD_BYTES"] = 20
    content = b"column\n" + (b"a" * 20) + b"\n"

    response = _upload(client, content)

    assert response.status_code == 413
    assert b"maximum size" in response.data.lower()
    assert _stored_files(app) == []


def test_too_many_rows_are_rejected(app: Flask, client: FlaskClient) -> None:
    app.config["MAX_CSV_ROWS"] = 2

    response = _upload(client, b"value\none\ntwo\nthree\n")

    assert response.status_code == 400
    assert b"maximum of 2 data rows" in response.data
    assert _stored_files(app) == []


def test_too_many_columns_are_rejected(app: Flask, client: FlaskClient) -> None:
    app.config["MAX_CSV_COLUMNS"] = 2

    response = _upload(client, b"one,two,three\n1,2,3\n")

    assert response.status_code == 400
    assert b"3 columns" in response.data
    assert b"maximum is 2" in response.data
    assert _stored_files(app) == []


def test_malformed_rows_produce_readable_error(app: Flask, client: FlaskClient) -> None:
    response = _upload(client, b"name,amount\nAlice,10\nBob\n")

    assert response.status_code == 400
    assert b"Malformed CSV row" in response.data
    assert b"expected 2 columns but found 1" in response.data
    assert _stored_files(app) == []


def test_unclosed_quoted_field_is_rejected(app: Flask, client: FlaskClient) -> None:
    response = _upload(client, b'name,note\nAlice,"unfinished\n')

    assert response.status_code == 400
    assert b"Malformed CSV" in response.data
    assert _stored_files(app) == []


def test_duplicate_columns_are_rejected(app: Flask, client: FlaskClient) -> None:
    response = _upload(client, b"Name, name \nAlice,Example\n")

    assert response.status_code == 400
    assert b"duplicate column names" in response.data
    assert _stored_files(app) == []


def test_path_traversal_filename_is_ignored(app: Flask, client: FlaskClient) -> None:
    upload_dir = Path(app.config["UPLOAD_DIR"])
    escaped_target = upload_dir.parent / "escaped.csv"

    response = _upload(client, b"name\nAlice\n", filename="../../escaped.csv")

    assert response.status_code == 200
    assert not escaped_target.exists()
    stored = _stored_files(app)
    assert len(stored) == 1
    assert stored[0].parent == upload_dir
    assert stored[0].name != "escaped.csv"


def test_html_and_script_cell_content_is_escaped(client: FlaskClient) -> None:
    response = _upload(client, b'name,note\nAlice,"<script>alert(1)</script>"\n')
    page = response.data.decode("utf-8")

    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page


def test_preview_is_limited_but_all_rows_are_counted(app: Flask, client: FlaskClient) -> None:
    app.config["CSV_PREVIEW_ROWS"] = 2

    response = _upload(client, b"name\nfirst-preview\nsecond-preview\nnot-previewed\n")

    assert response.status_code == 200
    assert b"first-preview" in response.data
    assert b"second-preview" in response.data
    assert b"not-previewed" not in response.data
    assert b"<dd>3</dd>" in response.data


def test_file_hash_is_reproducible_and_names_remain_random(
    app: Flask, client: FlaskClient
) -> None:
    content = b"name,amount\nAlice,10\n"
    expected_hash = hashlib.sha256(content).hexdigest()

    first_response = _upload(client, content)
    second_response = _upload(client, content)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert expected_hash.encode() in first_response.data
    assert expected_hash.encode() in second_response.data
    stored = _stored_files(app)
    assert len(stored) == 2
    assert stored[0].name != stored[1].name


def test_exactly_one_file_is_required(app: Flask, client: FlaskClient) -> None:
    response = client.post(
        "/upload",
        data={
            "file": [
                (io.BytesIO(b"name\nAlice\n"), "first.csv"),
                (io.BytesIO(b"name\nBob\n"), "second.csv"),
            ]
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 400
    assert b"exactly one CSV, JSON, or XLSX file" in response.data
    assert _stored_files(app) == []
