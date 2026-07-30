"""Milestone 6A persistent workspace metadata and route tests."""

import json
import re
from io import BytesIO
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from insight_reporter.dataset_ingestion import DatasetUploadResult
from insight_reporter.workspace_history import (
    WorkspaceDirectories,
    WorkspaceHistoryError,
    archive_workspace,
    archive_workspace_report,
    archive_workspace_source,
    attach_workspace_source,
    create_empty_workspace,
    create_workspace_record,
    get_workspace_summary,
    list_workspace_summaries,
    load_workspace_record,
    rename_workspace,
    rename_workspace_report,
    rename_workspace_source,
    restore_workspace,
    restore_workspace_report,
    restore_workspace_source,
    update_workspace_details,
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
        trash_dir=tmp_path / "trash",
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
    )["schema_version"] == 2


def test_empty_workspace_can_be_created_before_source_selection(
    tmp_path: Path,
) -> None:
    directories = _directories(tmp_path)

    created = create_empty_workspace(
        "Quarterly review",
        description="Management reporting",
        workspace_dir=directories.workspace_dir,
    )
    summary = get_workspace_summary(
        created.dataset_id,
        directories=directories,
    )

    assert re.fullmatch(r"[0-9a-f]{32}", created.dataset_id)
    assert created.schema_version == 2
    assert created.description == "Management reporting"
    assert created.has_source is False
    assert summary is not None
    assert summary.stage == "source_required"
    assert list_workspace_summaries(directories=directories) == (summary,)


def test_schema_one_workspace_is_read_and_migrated_on_edit(
    tmp_path: Path,
) -> None:
    directories = _directories(tmp_path)
    dataset_id = "e" * 32
    source = directories.upload_dir / f"{dataset_id}.csv"
    source.write_text("revenue\n10\n", encoding="utf-8")
    legacy_payload = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "name": "Legacy sales",
        "original_filename": "legacy-sales.csv",
        "internal_filename": source.name,
        "source_format": "csv",
        "source_sha256": "f" * 64,
        "size_bytes": source.stat().st_size,
        "created_at": "2026-07-29T00:00:00+00:00",
    }
    metadata_path = directories.workspace_dir / f"{dataset_id}.json"
    metadata_path.write_text(
        json.dumps(legacy_payload),
        encoding="utf-8",
    )

    loaded = load_workspace_record(
        dataset_id,
        workspace_dir=directories.workspace_dir,
    )
    migrated = update_workspace_details(
        dataset_id,
        name="Migrated sales",
        description="Adopted by 6A.1",
        workspace_dir=directories.workspace_dir,
    )

    assert loaded is not None
    assert loaded.schema_version == 1
    assert migrated.schema_version == 2
    assert migrated.description == "Adopted by 6A.1"
    assert json.loads(
        metadata_path.read_text(encoding="utf-8")
    )["schema_version"] == 2


def test_workspace_source_report_and_workspace_lifecycle_is_recoverable(
    tmp_path: Path,
) -> None:
    directories = _directories(tmp_path)
    record = create_empty_workspace(
        "Sales review",
        workspace_dir=directories.workspace_dir,
    )
    source = directories.upload_dir / f"{record.dataset_id}.csv"
    source.write_text("region,revenue\nNorth,10\n", encoding="utf-8")
    upload = DatasetUploadResult(
        internal_filename=source.name,
        source_format="csv",
        sha256="d" * 64,
        size_bytes=source.stat().st_size,
        row_count=1,
        column_count=2,
    )

    attached = attach_workspace_source(
        record.dataset_id,
        upload,
        original_filename="sales.csv",
        workspace_dir=directories.workspace_dir,
    )
    renamed_source = rename_workspace_source(
        record.dataset_id,
        "FY26 sales.csv",
        workspace_dir=directories.workspace_dir,
    )
    archived_source = archive_workspace_source(
        record.dataset_id,
        workspace_dir=directories.workspace_dir,
        upload_dir=directories.upload_dir,
        trash_dir=directories.trash_dir,
    )

    assert attached.has_source
    assert renamed_source.original_filename == "FY26 sales.csv"
    assert archived_source.source_archived_at is not None
    assert not source.exists()
    assert (
        directories.trash_dir
        / "sources"
        / record.dataset_id
        / source.name
    ).is_file()

    restored_source = restore_workspace_source(
        record.dataset_id,
        workspace_dir=directories.workspace_dir,
        upload_dir=directories.upload_dir,
        trash_dir=directories.trash_dir,
    )
    report_id = "RPT-AAAAAAAAAAAAAAAA"
    report_dir = directories.generated_report_dir / record.dataset_id
    report_dir.mkdir()
    (report_dir / f"V0001-{report_id}.json").write_text(
        "{}",
        encoding="utf-8",
    )
    renamed_report = rename_workspace_report(
        record.dataset_id,
        report_id,
        "Board sales brief",
        workspace_dir=directories.workspace_dir,
    )
    archived_report = archive_workspace_report(
        record.dataset_id,
        report_id,
        workspace_dir=directories.workspace_dir,
    )
    archived_summary = get_workspace_summary(
        record.dataset_id,
        directories=directories,
    )

    assert restored_source.has_source
    assert source.is_file()
    assert renamed_report.report_names[report_id] == "Board sales brief"
    assert (
        report_dir / f"V0001-{report_id}.json"
    ).read_text(encoding="utf-8") == "{}"
    assert archived_report.archived_report_ids == (report_id,)
    assert archived_summary is not None
    assert archived_summary.report_run_count == 0
    assert archived_summary.archived_report_run_count == 1

    restored_report = restore_workspace_report(
        record.dataset_id,
        report_id,
        workspace_dir=directories.workspace_dir,
    )
    archived_workspace = archive_workspace(
        record.dataset_id,
        workspace_dir=directories.workspace_dir,
    )

    assert restored_report.archived_report_ids == ()
    assert archived_workspace.is_archived
    assert list_workspace_summaries(directories=directories) == ()
    assert len(
        list_workspace_summaries(
            directories=directories,
            include_archived=True,
        )
    ) == 1

    restored_workspace = restore_workspace(
        record.dataset_id,
        workspace_dir=directories.workspace_dir,
    )
    edited_workspace = update_workspace_details(
        record.dataset_id,
        name="Executive sales review",
        description="Decisions for the next quarter",
        workspace_dir=directories.workspace_dir,
    )

    assert not restored_workspace.is_archived
    assert edited_workspace.name == "Executive sales review"
    assert edited_workspace.description == "Decisions for the next quarter"


def test_source_archive_rolls_back_when_metadata_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directories = _directories(tmp_path)
    dataset_id = "1" * 32
    source = directories.upload_dir / f"{dataset_id}.csv"
    source.write_text("revenue\n10\n", encoding="utf-8")
    create_workspace_record(
        DatasetUploadResult(
            internal_filename=source.name,
            source_format="csv",
            sha256="2" * 64,
            size_bytes=source.stat().st_size,
            row_count=1,
            column_count=1,
        ),
        original_filename="sales.csv",
        workspace_dir=directories.workspace_dir,
    )

    def fail_save(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise WorkspaceHistoryError("Workspace metadata could not be saved.")

    monkeypatch.setattr(
        "insight_reporter.workspace_history._save_workspace_record",
        fail_save,
    )

    with pytest.raises(
        WorkspaceHistoryError,
        match="metadata could not be saved",
    ):
        archive_workspace_source(
            dataset_id,
            workspace_dir=directories.workspace_dir,
            upload_dir=directories.upload_dir,
            trash_dir=directories.trash_dir,
        )

    assert source.is_file()
    assert not (
        directories.trash_dir / "sources" / dataset_id / source.name
    ).exists()


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
    assert b"1. Data source" in detail.data
    assert b"2. KPI configuration and deterministic evidence" in detail.data
    assert b"3. Dashboard and visualizations" in detail.data
    assert b"4. Next actions" in detail.data
    assert b"5. Report revisions" in detail.data
    assert b"Open dashboard" in detail.data
    assert b"Delete workspace and all recoverably" in detail.data
    assert renamed.status_code == 303
    assert b"Regional performance" in client.get(
        f"/workspaces/{dataset_id}"
    ).data


def test_workspace_first_route_creates_then_attaches_a_source(
    app: Flask,
    client: FlaskClient,
) -> None:
    created = client.post(
        "/workspaces",
        data={
            "name": "Management sales",
            "description": "Quarterly decision support",
        },
    )

    assert created.status_code == 303
    match = re.search(
        r"/workspaces/([0-9a-f]{32})$",
        created.headers["Location"],
    )
    assert match is not None
    dataset_id = match.group(1)

    empty_detail = client.get(created.headers["Location"])
    source_form = client.get(f"/workspaces/{dataset_id}/source")
    attached = client.post(
        f"/workspaces/{dataset_id}/source",
        data={
            "file": (
                BytesIO(b"region,revenue\nNorth,10\nSouth,20\n"),
                "quarterly-sales.csv",
            )
        },
        content_type="multipart/form-data",
    )

    assert empty_detail.status_code == 200
    assert b"No source file has been selected" in empty_detail.data
    assert b"No reports exist yet" not in empty_detail.data
    assert source_form.status_code == 200
    assert attached.status_code == 303
    assert attached.headers["Location"] == f"/dataset/{dataset_id}"
    assert (
        Path(app.config["UPLOAD_DIR"]) / f"{dataset_id}.csv"
    ).is_file()
    record = load_workspace_record(
        dataset_id,
        workspace_dir=Path(app.config["WORKSPACE_DIR"]),
    )
    assert record is not None
    assert record.original_filename == "quarterly-sales.csv"
    assert record.name == "Management sales"

    renamed = client.post(
        f"/workspaces/{dataset_id}/source/name",
        data={"name": "FY26 quarterly sales.csv"},
    )
    deleted = client.post(f"/workspaces/{dataset_id}/source/archive")
    deleted_detail = client.get(f"/workspaces/{dataset_id}")
    restored = client.post(f"/workspaces/{dataset_id}/source/restore")

    assert renamed.status_code == 303
    assert deleted.status_code == 303
    assert b"moved to recoverable trash" in deleted_detail.data
    assert restored.status_code == 303
    assert (
        Path(app.config["UPLOAD_DIR"]) / f"{dataset_id}.csv"
    ).is_file()


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
