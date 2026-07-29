"""Verify that the configured local Ollama model can answer one request."""

import os

from ollama import Client

HOST = "http://127.0.0.1:11434"
MODEL = os.getenv("APP_OLLAMA_MODEL", "llama3.2:latest")
PROMPT = os.getenv(
    "OLLAMA_CHECK_PROMPT",
    "Reply with a short confirmation that local model inference is working.",
)


def main() -> None:
    try:
        response = Client(host=HOST).chat(
            model=MODEL,
            messages=[{"role": "user", "content": PROMPT}],
        )
        print(response["message"]["content"])
    except Exception as error:
        raise SystemExit(
            f"Could not use {MODEL} through Ollama at {HOST}: {error}"
        ) from error


if __name__ == "__main__":
    main()
