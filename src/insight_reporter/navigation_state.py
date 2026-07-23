"""Small server-side UI state used by POST/Redirect/GET navigation."""

import json
import re
import secrets
import time
from pathlib import Path

_MAX_STATE_BYTES = 100_000
_RETENTION_SECONDS = 24 * 60 * 60


class NavigationStateError(ValueError):
    """Raised when transient navigation state is invalid or unavailable."""


def save_navigation_state(payload: dict[str, object], *, state_dir: Path) -> str:
    """Atomically retain validated UI state outside Flask's static directory."""

    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_STATE_BYTES:
        raise NavigationStateError("Navigation state is too large.")

    state_dir.mkdir(parents=True, exist_ok=True)
    _delete_expired_states(state_dir)
    token = secrets.token_hex(16)
    final_path = state_dir / f"{token}.json"
    temporary_path = state_dir / f".{token}.{secrets.token_hex(8)}.part"
    try:
        temporary_path.write_text(encoded, encoding="utf-8")
        temporary_path.replace(final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return token


def load_navigation_state(token: str, *, state_dir: Path) -> dict[str, object]:
    """Load recent state addressed by an unguessable, path-safe token."""

    if re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise NavigationStateError("Navigation state token is invalid.")
    path = state_dir / f"{token}.json"
    try:
        if time.time() - path.stat().st_mtime > _RETENTION_SECONDS:
            path.unlink(missing_ok=True)
            raise NavigationStateError("Navigation state has expired.")
        raw = path.read_bytes()
    except OSError as error:
        raise NavigationStateError("Navigation state is unavailable.") from error
    if len(raw) > _MAX_STATE_BYTES:
        raise NavigationStateError("Navigation state is too large.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NavigationStateError("Navigation state is unreadable.") from error
    if not isinstance(payload, dict):
        raise NavigationStateError("Navigation state has an invalid shape.")
    return payload


def _delete_expired_states(state_dir: Path) -> None:
    cutoff = time.time() - _RETENTION_SECONDS
    for path in state_dir.glob("[0-9a-f]" * 32 + ".json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue
