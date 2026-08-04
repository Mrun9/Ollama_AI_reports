"""Persistent artifacts for the interactive manual visualization board."""

from __future__ import annotations

import base64
import json
import re
import secrets
import struct
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from insight_reporter.manual_visualization_preview import MANUAL_CHART_TYPES

_DATASET_ID = re.compile(r"[0-9a-f]{32}")
_VISUALIZATION_ID = re.compile(r"MBV-[0-9A-F]{16}")
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_MAX_SVG_BYTES = 512 * 1024
_MAX_PNG_BYTES = 2 * 1024 * 1024
_MAX_TITLE_CHARACTERS = 120
_FIELD_ROLES = ("x", "y", "series", "size", "secondary_y")
_PARETO_LINE_MODES = frozenset(
    {"cumulative_percent", "cumulative_value", "individual_percent", "none"}
)
_SVG_ELEMENTS = frozenset(
    {"svg", "title", "desc", "g", "line", "rect", "circle", "path", "polyline", "polygon", "text"}
)
_SVG_ATTRIBUTES = frozenset(
    {
        "id",
        "viewBox",
        "role",
        "aria-labelledby",
        "x",
        "y",
        "x1",
        "x2",
        "y1",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "width",
        "height",
        "d",
        "points",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-dasharray",
        "opacity",
        "text-anchor",
        "font-size",
        "font-weight",
        "transform",
        "class",
    }
)


class ManualVisualizationStoreError(ValueError):
    """Raised when a manual-board artifact is invalid or unavailable."""


@dataclass(frozen=True)
class ManualVisualizationArtifact:
    schema_version: int
    visualization_id: str
    dataset_id: str
    title: str
    requested_chart: str
    chart_type: str
    fields: dict[str, str | None]
    settings: dict[str, object]
    preview: dict[str, Any]
    source_sha256: str
    svg_filename: str
    png_filename: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "visualization_id": self.visualization_id,
            "dataset_id": self.dataset_id,
            "title": self.title,
            "requested_chart": self.requested_chart,
            "chart_type": self.chart_type,
            "fields": self.fields,
            "settings": self.settings,
            "preview": self.preview,
            "source_sha256": self.source_sha256,
            "svg_filename": self.svg_filename,
            "png_filename": self.png_filename,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def save_manual_visualization(
    *,
    dataset_id: str,
    source_sha256: str,
    values: Mapping[str, object],
    preview: Mapping[str, Any],
    svg_markup: str,
    visualization_dir: Path,
    png_data_url: str | None = None,
) -> ManualVisualizationArtifact:
    """Validate and atomically save one interactive manual visualization."""

    _validate_dataset_id(dataset_id)
    requested_id = _optional_text(values.get("visualization_id"))
    if requested_id:
        _validate_visualization_id(requested_id)
        existing = load_manual_visualization(
            requested_id,
            dataset_id=dataset_id,
            source_sha256=source_sha256,
            visualization_dir=visualization_dir,
        )
        visualization_id = requested_id
        created_at = existing.created_at
    else:
        visualization_id = f"MBV-{secrets.token_hex(8).upper()}"
        created_at = _timestamp()

    title = _required_text(values.get("title"), label="Visualization title")
    if len(title) > _MAX_TITLE_CHARACTERS:
        raise ManualVisualizationStoreError(
            f"Visualization title must be {_MAX_TITLE_CHARACTERS} characters or fewer."
        )
    requested_chart = _required_text(values.get("chart"), label="Chart type")
    if requested_chart not in MANUAL_CHART_TYPES:
        raise ManualVisualizationStoreError("The requested chart type is unsupported.")
    effective_chart = str(preview.get("chart_type", ""))
    if effective_chart not in MANUAL_CHART_TYPES - {"auto"}:
        raise ManualVisualizationStoreError("The preview chart type is unsupported.")

    raw_fields = values.get("fields")
    if not isinstance(raw_fields, Mapping):
        raise ManualVisualizationStoreError("Visualization fields are missing.")
    fields = {
        role: _optional_text(raw_fields.get(role))
        for role in _FIELD_ROLES
    }
    raw_settings = values.get("settings")
    if not isinstance(raw_settings, Mapping):
        raw_settings = {}
    pareto_line = _optional_text(raw_settings.get("pareto_line")) or "cumulative_percent"
    if pareto_line not in _PARETO_LINE_MODES:
        raise ManualVisualizationStoreError("The Pareto line setting is unsupported.")
    target = raw_settings.get("target")
    settings: dict[str, object] = {
        "pareto_line": pareto_line,
        "target": float(target) if isinstance(target, (int, float)) else None,
    }

    safe_svg = sanitize_manual_visualization_svg(svg_markup)
    png_bytes = _manual_visualization_png(png_data_url) if png_data_url else None
    now = _timestamp()
    svg_filename = f"{visualization_id}.svg"
    png_filename = f"{visualization_id}.png" if png_bytes is not None else None
    artifact = ManualVisualizationArtifact(
        schema_version=1,
        visualization_id=visualization_id,
        dataset_id=dataset_id,
        title=title,
        requested_chart=requested_chart,
        chart_type=effective_chart,
        fields=fields,
        settings=settings,
        preview=dict(preview),
        source_sha256=source_sha256,
        svg_filename=svg_filename,
        png_filename=png_filename,
        created_at=created_at,
        updated_at=now,
    )
    directory = _artifact_directory(visualization_dir, dataset_id)
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_write(directory / svg_filename, safe_svg)
    if png_bytes is not None and png_filename is not None:
        _atomic_write_bytes(directory / png_filename, png_bytes)
    _atomic_write(
        directory / f"{visualization_id}.json",
        json.dumps(artifact.to_dict(), indent=2, sort_keys=True),
    )
    return artifact


def load_manual_visualization(
    visualization_id: str,
    *,
    dataset_id: str,
    source_sha256: str,
    visualization_dir: Path,
) -> ManualVisualizationArtifact:
    """Load one artifact only when it still matches the current source."""

    _validate_dataset_id(dataset_id)
    _validate_visualization_id(visualization_id)
    path = _artifact_directory(visualization_dir, dataset_id) / f"{visualization_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ManualVisualizationStoreError("Manual visualization was not found.") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ManualVisualizationStoreError(
            "Manual visualization could not be loaded safely."
        ) from error
    artifact = _artifact_from_payload(payload)
    if artifact.dataset_id != dataset_id or artifact.visualization_id != visualization_id:
        raise ManualVisualizationStoreError("Manual visualization identity is invalid.")
    if artifact.source_sha256 != source_sha256:
        raise ManualVisualizationStoreError(
            "Manual visualization belongs to an earlier data source."
        )
    return artifact


def list_manual_visualizations(
    *,
    dataset_id: str,
    source_sha256: str,
    visualization_dir: Path,
) -> tuple[ManualVisualizationArtifact, ...]:
    """Return current-source manual-board artifacts, newest first."""

    _validate_dataset_id(dataset_id)
    directory = _artifact_directory(visualization_dir, dataset_id)
    artifacts: list[ManualVisualizationArtifact] = []
    for path in directory.glob("MBV-*.json") if directory.is_dir() else ():
        try:
            artifact = load_manual_visualization(
                path.stem,
                dataset_id=dataset_id,
                source_sha256=source_sha256,
                visualization_dir=visualization_dir,
            )
        except ManualVisualizationStoreError:
            continue
        artifacts.append(artifact)
    return tuple(sorted(artifacts, key=lambda item: item.updated_at, reverse=True))


def manual_visualization_svg_path(
    artifact: ManualVisualizationArtifact,
    *,
    visualization_dir: Path,
) -> Path:
    path = _artifact_directory(visualization_dir, artifact.dataset_id) / artifact.svg_filename
    if not path.is_file():
        raise ManualVisualizationStoreError("Manual visualization chart was not found.")
    return path


def manual_visualization_png_path(
    artifact: ManualVisualizationArtifact,
    *,
    visualization_dir: Path,
) -> Path | None:
    if artifact.png_filename is None:
        return None
    path = _artifact_directory(visualization_dir, artifact.dataset_id) / artifact.png_filename
    return path if path.is_file() else None


def sanitize_manual_visualization_svg(markup: str) -> str:
    """Accept only the small SVG subset emitted by the local chart renderer."""

    encoded = markup.encode("utf-8")
    if not encoded or len(encoded) > _MAX_SVG_BYTES:
        raise ManualVisualizationStoreError("Visualization SVG is empty or too large.")
    if "<!" in markup:
        raise ManualVisualizationStoreError("Visualization SVG declarations are prohibited.")
    try:
        root = ET.fromstring(markup)  # noqa: S314 - bounded input; declarations rejected above.
    except ET.ParseError as error:
        raise ManualVisualizationStoreError("Visualization SVG is malformed.") from error
    if _local_name(root.tag) != "svg" or root.attrib.get("viewBox") != "0 0 800 460":
        raise ManualVisualizationStoreError("Visualization SVG root is invalid.")
    for element in root.iter():
        if _local_name(element.tag) not in _SVG_ELEMENTS:
            raise ManualVisualizationStoreError("Visualization SVG contains an unsafe element.")
        if element.text is not None and len(element.text) > 500:
            raise ManualVisualizationStoreError("Visualization SVG text is too long.")
        for name, value in element.attrib.items():
            if _local_name(name) not in _SVG_ATTRIBUTES:
                raise ManualVisualizationStoreError(
                    "Visualization SVG contains an unsafe attribute."
                )
            lowered = value.casefold()
            if len(value) > 20_000 or "javascript:" in lowered or "url(" in lowered:
                raise ManualVisualizationStoreError(
                    "Visualization SVG contains an unsafe attribute value."
                )
    ET.register_namespace("", _SVG_NAMESPACE)
    return ET.tostring(root, encoding="unicode")


def _artifact_from_payload(payload: object) -> ManualVisualizationArtifact:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ManualVisualizationStoreError("Manual visualization schema is unsupported.")
    try:
        fields = dict(payload["fields"])
        settings = dict(payload["settings"])
        preview = dict(payload["preview"])
        visualization_id = str(payload["visualization_id"])
        svg_filename = str(payload["svg_filename"])
        png_filename = (
            str(payload["png_filename"])
            if payload.get("png_filename") is not None
            else None
        )
        if svg_filename != f"{visualization_id}.svg" or (
            png_filename is not None
            and png_filename != f"{visualization_id}.png"
        ):
            raise ValueError("invalid chart filename")
        return ManualVisualizationArtifact(
            schema_version=1,
            visualization_id=visualization_id,
            dataset_id=str(payload["dataset_id"]),
            title=str(payload["title"]),
            requested_chart=str(payload["requested_chart"]),
            chart_type=str(payload["chart_type"]),
            fields={role: fields.get(role) for role in _FIELD_ROLES},
            settings=settings,
            preview=preview,
            source_sha256=str(payload["source_sha256"]),
            svg_filename=svg_filename,
            png_filename=png_filename,
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ManualVisualizationStoreError(
            "Manual visualization payload is invalid."
        ) from error


def _artifact_directory(visualization_dir: Path, dataset_id: str) -> Path:
    return visualization_dir / "manual_boards" / dataset_id


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ManualVisualizationStoreError(
            "Manual visualization could not be saved safely."
        ) from error


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ManualVisualizationStoreError(
            "Manual visualization PNG could not be saved safely."
        ) from error


def _manual_visualization_png(data_url: str) -> bytes:
    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix):
        raise ManualVisualizationStoreError("Visualization PNG is invalid.")
    try:
        content = base64.b64decode(data_url[len(prefix):], validate=True)
    except (ValueError, TypeError) as error:
        raise ManualVisualizationStoreError("Visualization PNG is invalid.") from error
    if len(content) < 24 or len(content) > _MAX_PNG_BYTES:
        raise ManualVisualizationStoreError("Visualization PNG is empty or too large.")
    if content[:8] != b"\x89PNG\r\n\x1a\n" or content[12:16] != b"IHDR":
        raise ManualVisualizationStoreError("Visualization PNG signature is invalid.")
    width, height = struct.unpack(">II", content[16:24])
    if (width, height) != (800, 460):
        raise ManualVisualizationStoreError("Visualization PNG dimensions are invalid.")
    return content


def _validate_dataset_id(dataset_id: str) -> None:
    if _DATASET_ID.fullmatch(dataset_id) is None:
        raise ManualVisualizationStoreError("Dataset identifier is invalid.")


def _validate_visualization_id(visualization_id: str) -> None:
    if _VISUALIZATION_ID.fullmatch(visualization_id) is None:
        raise ManualVisualizationStoreError("Manual visualization identifier is invalid.")


def _required_text(value: object, *, label: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ManualVisualizationStoreError(f"{label} is required.")
    return text


def _optional_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()
