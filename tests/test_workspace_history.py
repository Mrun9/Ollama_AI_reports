"""Milestone 6A persistent workspace metadata and route tests."""

import json
import re
from io import BytesIO
from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient

from insight_reporter.dataset_ingestion import DatasetUploadResult
from insight_reporter.workspace_history import (
    WorkspaceDirectories,
    WorkspaceHistoryError,
    create_workspace_record,
    get_workspace_summary,
    list_workspace_summaries,
    load_workspace_record,
    rename_workspace,
)


def _directories(tmp_path: Path) -> WorkspaceDirectories:
    directories = WorkspaceDirectories(
        upload_dir=tmp_path / "uploads",
        workspace_dir=tmp_path / "workspaces",
        configuration_dir=tmp_path / "configurations",
        insight_dir=tmp_path / "insights",
        evidence_dir=tmp_path / "evidence",
        visualization_dir=tmp_path / "visualizations",
        report_configuration_dir=tmp_path / "report_configurations",
        report_package_dir=tmp_path / "report_packages",
        generated_report_dir=tmp_path / "generated_reports",
    )
    for directory in directories.__dict__.values():
        directory.mkdir(parents=True)
    return directories


def test_workspace_metadata_is_versioned_safe_and_renameable(
    tmp_path: Path,
) -> None:
    directories = _directories(tmp_path)
    dataset_id = "a" * 32
    source = directories.upload_dir / f"{dataset_id}.csv"
    source.write_text("region,revenue\nNorth,10\n", encoding="utf-8")
    upload = DatasetUploadResult(
        internal_filename=source.name,
        source_format="csv",
        sha256="b" * 64,
        size_bytes=source.stat().st_size,
        row_count=1,
        column_count=2,
    )

    created = create_workspace_record(
        upload,
        original_filename="../../Quarterly Sales.csv",
        workspace_dir=directories.workspace_dir,
    )
    loaded = load_workspace_record(
        dataset_id,
        workspace_dir=directories.workspace_dir,
    )
    renamed = rename_workspace(
        dataset_id,
        "Executive reporting workspace",
        workspace_dir=directories.workspace_dir,
    )

    assert created.name == "Quarterly Sales"
    assert created.original_filename == "Quarterly Sales.csv"
    assert loaded == created
    assert renamed.name == "Executive reporting workspace"
    assert json.loads(
        (directories.workspace_dir / f"{dataset_id}.json").read_text(
            encoding="utf-8"
        )
    )["schema_version"] == 1


def test_workspace_summary_reconstructs_progress_and_legacy_uploads(
    tmp_path: Path,
) -> None:
    directories = _directories(tmp_path)
    current_id = "a" * 32
    legacy_id = "b" * 32
    current_source = directories.upload_dir / f"{current_id}.json"
    current_source.write_text('[{"revenue": 10}]', encoding="utf-8")
    legacy_source = directories.upload_dir / f"{legacy_id}.csv"
    legacy_source.write_text("revenue\n10\n", encoding="utf-8")
    create_workspace_record(
        DatasetUploadResult(
            internal_filename=current_source.name,
            source_format="json",
            sha256="c" * 64,
            size_bytes=current_source.stat().st_size,
            row_count=1,
            column_count=1,
        ),
        original_filename="sales.json",
        workspace_dir=directories.workspace_dir,
    )
    report_dir = directories.generated_report_dir / current_id
    report_dir.mkdir()
    (report_dir / "V0001-RPT-AAAAAAAAAAAAAAAA.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (report_dir / "V0002-RPT-AAAAAAAAAAAAAAAA.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (report_dir / "V0003-RPT-BBBBBBBBBBBBBBBB.json").write_text(
        "{}",
        encoding="utf-8",
    )

    current = get_workspace_summary(current_id, directories=directories)
    legacy = get_workspace_summary(legacy_id, directories=directories)
    all_workspaces = list_workspace_summaries(directories=directories)

    assert current is not None
    assert current.stage == "report_generated"
    assert current.report_version_count == 3
    assert current.report_run_count == 2
    assert current.metadata_warning is None
    assert legacy is not None
    assert legacy.record.name == f"Dataset {legacy_id[:8]}"
    assert legacy.metadata_warning is not None
    adopted = rename_workspace(
        legacy_id,
        "Adopted legacy workspace",
        workspace_dir=directories.workspace_dir,
        fallback_record=legacy.record,
    )
    assert adopted.name == "Adopted legacy workspace"
    assert load_workspace_record(
        legacy_id,
        workspace_dir=directories.workspace_dir,
    ) == adopted
    assert {item.record.dataset_id for item in all_workspaces} == {
        current_id,
        legacy_id,
    }


def test_upload_creates_reopenable_workspace(
    app: Flask,
    client: FlaskClient,
) -> None:
    response = client.post(
        "/upload",
        data={
            "file": (
                BytesIO(b"region,revenue\nNorth,10\nSouth,20\n"),
                "regional-sales.csv",
            )
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 303
    match = re.search(r"/dataset/([0-9a-f]{32})$", response.headers["Location"])
    assert match is not None
    dataset_id = match.group(1)
    metadata_path = (
        Path(app.config["WORKSPACE_DIR"]) / f"{dataset_id}.json"
    )
    assert metadata_path.is_file()
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["name"] == (
        "regional-sales"
    )

    history = client.get("/workspaces")
    detail = client.get(f"/workspaces/{dataset_id}")
    renamed = client.post(
        f"/workspaces/{dataset_id}/name",
        data={"name": "Regional performance"},
    )

    assert history.status_code == 200
    assert b"regional-sales" in history.data
    assert detail.status_code == 200
    assert b"Dataset uploaded" in detail.data
    assert renamed.status_code == 303
    assert b"Regional performance" in client.get(
        f"/workspaces/{dataset_id}"
    ).data


def test_workspace_metadata_failure_rolls_back_uploaded_source(
    app: Flask,
    client: FlaskClient,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    def fail_workspace_save(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise WorkspaceHistoryError("Workspace metadata could not be saved.")

    monkeypatch.setattr(
        "insight_reporter.routes.create_workspace_record",
        fail_workspace_save,
    )
    response = client.post(
        "/upload",
        data={
            "file": (
                BytesIO(b"region,revenue\nNorth,10\n"),
                "sales.csv",
            )
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 500
    assert b"Workspace metadata could not be saved" in response.data
    assert tuple(Path(app.config["UPLOAD_DIR"]).iterdir()) == ()
