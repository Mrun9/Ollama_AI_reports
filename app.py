import os

from ollama import Client

HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
PROMPT = os.getenv(
    "PROMPT",
    (
        "Hello! Which open-source models available through Ollama are best for generating "
        "reports from raw CSV or JSON data?"
    ),
)


def main() -> None:
    client = Client(host=HOST)

    try:
        response = client.chat(
            model=MODEL,
            messages=[{"role": "user", "content": PROMPT}],
        )
        print(response["message"]["content"])
    except Exception as exc:
        message = (
            "Could not connect to Ollama. Make sure the server is running and the model is "
            f"pulled. Error: {exc}"
        )
        print(message)


if __name__ == "__main__":
    main()
