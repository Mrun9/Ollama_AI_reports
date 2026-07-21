"""Validated business selections derived from an actual dataset profile."""

import json
import math
import re
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from insight_reporter.dataset_profile import DatasetProfile

_DIRECTIONS = frozenset({"higher", "lower"})
_MAX_OBJECTIVE_CHARACTERS = 2_000


class BusinessConfigurationError(ValueError):
    """Raised when a user selection does not match the retained dataset."""


@dataclass(frozen=True)
class BusinessConfiguration:
    schema_version: int
    dataset_id: str
    source_sha256: str
    primary_kpi: str
    kpi_direction: str
    date_column: str | None
    category_columns: tuple[str, ...]
    target_or_benchmark: float | None
    business_objective: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def validate_business_configuration(
    profile: DatasetProfile,
    *,
    dataset_id: str,
    primary_kpi: str,
    kpi_direction: str,
    date_column: str,
    category_columns: list[str],
    target_or_benchmark: str,
    business_objective: str,
) -> BusinessConfiguration:
    """Create a configuration only from candidates present in the profile."""

    if re.fullmatch(r"[0-9a-f]{32}", dataset_id) is None:
        raise BusinessConfigurationError("Dataset ID is invalid.")

    if primary_kpi not in profile.kpi_candidates:
        raise BusinessConfigurationError("Select a measurable KPI from the available candidates.")

    if kpi_direction not in _DIRECTIONS:
        raise BusinessConfigurationError("KPI direction must be either higher or lower.")

    selected_date = date_column.strip() or None
    if selected_date is not None and selected_date not in profile.date_candidates:
        raise BusinessConfigurationError("Selected date column is not a valid date candidate.")

    if len(category_columns) != len(set(category_columns)):
        raise BusinessConfigurationError("Category selections must not contain duplicates.")
    invalid_categories = [
        column for column in category_columns if column not in profile.category_candidates
    ]
    if invalid_categories:
        raise BusinessConfigurationError(
            "Category selections must come from the available category candidates."
        )

    target = _parse_optional_target(target_or_benchmark)
    objective = business_objective.strip()
    if not objective:
        raise BusinessConfigurationError("Business objective is required.")
    if len(objective) > _MAX_OBJECTIVE_CHARACTERS:
        raise BusinessConfigurationError(
            f"Business objective must be at most {_MAX_OBJECTIVE_CHARACTERS} characters."
        )

    return BusinessConfiguration(
        schema_version=1,
        dataset_id=dataset_id,
        source_sha256=profile.source_sha256,
        primary_kpi=primary_kpi,
        kpi_direction=kpi_direction,
        date_column=selected_date,
        category_columns=tuple(category_columns),
        target_or_benchmark=target,
        business_objective=objective,
    )


def save_business_configuration(
    configuration: BusinessConfiguration, *, configuration_dir: Path
) -> Path:
    """Atomically persist non-sensitive selections outside the static tree."""

    configuration_dir.mkdir(parents=True, exist_ok=True)
    final_path = configuration_dir / f"{configuration.dataset_id}.json"
    temporary_path = configuration_dir / (
        f".{configuration.dataset_id}.{secrets.token_hex(8)}.part"
    )
    payload = json.dumps(
        configuration.to_dict(),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )

    try:
        temporary_path.write_text(f"{payload}\n", encoding="utf-8")
        temporary_path.replace(final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return final_path


def _parse_optional_target(value: str) -> float | None:
    candidate = value.strip()
    if not candidate:
        return None
    try:
        target = float(candidate)
    except ValueError as error:
        raise BusinessConfigurationError("Target or benchmark must be a number.") from error
    if not math.isfinite(target):
        raise BusinessConfigurationError("Target or benchmark must be a finite number.")
    return target
