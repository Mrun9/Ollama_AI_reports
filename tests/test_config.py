"""Security-default tests."""

from flask import Flask


def test_safe_defaults(app: Flask) -> None:
    assert app.config["DEBUG"] is False
    assert app.config["HOST"] == "127.0.0.1"
    assert app.config["OLLAMA_HOST"] == "http://127.0.0.1:11434"
    assert app.config["MAX_UPLOAD_BYTES"] == 10 * 1024 * 1024
    assert app.config["MAX_CONTENT_LENGTH"] == 11 * 1024 * 1024
    assert app.config["MAX_CSV_ROWS"] == 5_000
    assert app.config["MAX_CSV_COLUMNS"] == 200
    assert app.config["CSV_PREVIEW_ROWS"] == 5
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_remote_ollama_host_cannot_be_set_through_environment(monkeypatch, app: Flask) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "https://example.invalid")

    assert app.config["OLLAMA_HOST"] == "http://127.0.0.1:11434"
