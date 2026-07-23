"""Transient server-side navigation-state tests."""

import os
import time
from pathlib import Path

import pytest

from insight_reporter.navigation_state import (
    NavigationStateError,
    load_navigation_state,
    save_navigation_state,
)


def test_navigation_state_round_trip_is_reusable_for_browser_history(
    tmp_path: Path,
) -> None:
    token = save_navigation_state(
        {"view": "profile", "dataset_id": "a" * 32}, state_dir=tmp_path
    )

    assert load_navigation_state(token, state_dir=tmp_path)["view"] == "profile"
    assert load_navigation_state(token, state_dir=tmp_path)["dataset_id"] == "a" * 32


def test_invalid_navigation_token_cannot_traverse_paths(tmp_path: Path) -> None:
    with pytest.raises(NavigationStateError, match="token"):
        load_navigation_state("../../configuration", state_dir=tmp_path)


def test_expired_navigation_state_is_deleted(tmp_path: Path) -> None:
    token = save_navigation_state({"view": "profile"}, state_dir=tmp_path)
    path = tmp_path / f"{token}.json"
    expired = time.time() - (25 * 60 * 60)
    os.utime(path, (expired, expired))

    with pytest.raises(NavigationStateError, match="expired"):
        load_navigation_state(token, state_dir=tmp_path)

    assert not path.exists()
