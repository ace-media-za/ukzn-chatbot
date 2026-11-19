# University of KwaZulu-Natal Chat API

A FastAPI service that streams conversations from a locally running Ollama model. It does not use PostgreSQL or a knowledge-base search.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com/) running locally
- The chat model configured in `.env` (default: `qwen2.5:1.5b`)

Pull the default model once:

```bash
ollama pull qwen2.5:1.5b
```

If Ollama is not already running as an app or service, start it in a separate terminal:

```bash
ollama serve
```

## Quick start

```bash
# Create and install the Python environment.
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Create your local configuration.
cp .env.example .env

# Start the API with auto-reload for development.
.venv/bin/fastapi dev main.py
```

The API is available at `http://127.0.0.1:8000`. Interactive API documentation is available at `http://127.0.0.1:8000/docs`.

## Configuration

Settings come from `.env`; environment variables take precedence. The defaults work for a local Ollama installation:

```dotenv
OLLAMA_HOST=http://localhost:11434
OLLAMA_CHAT_MODEL=qwen2.5:1.5b
```

To use a different model, pull it and update the setting:

```bash
ollama pull qwen2.5:7b
```

```dotenv
OLLAMA_CHAT_MODEL=qwen2.5:7b
```

Restart FastAPI after changing `.env`.

## Check Ollama

Use the health endpoint to confirm the API can reach Ollama and that the configured model is installed:

```bash
curl http://127.0.0.1:8000/health
```

Example response:

```json
{
  "status": "ok",
  "ollama_missing_models": []
}
```

If `ollama_missing_models` contains a model name, pull that model with `ollama pull <model-name>`.

## Chat

`POST /chat` accepts the conversation history and streams back plain-text response chunks. Use `-N` with `curl` to show chunks as they arrive:

```bash
curl -N http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [
      {"role": "user", "content": "What can you help me with?"}
    ]
  }'
```

For a follow-up message, include both sides of the earlier conversation:

```bash
curl -N http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [
      {"role": "user", "content": "Explain what an API is."},
      {"role": "assistant", "content": "An API is a way for software systems to communicate."},
      {"role": "user", "content": "Can you make that simpler?"}
    ]
  }'
```

The valid roles are `user` and `assistant`. Each message must have non-empty `content`; system prompts are owned by the server.

## Python client example

```python
import httpx

payload = {
    "messages": [
        {"role": "user", "content": "Say hello in isiZulu."},
    ],
}

with httpx.stream("POST", "http://127.0.0.1:8000/chat", json=payload) as response:
    response.raise_for_status()
    for text in response.iter_text():
        print(text, end="", flush=True)
```

## Common errors

```text
502 LLM unavailable
```

Start Ollama with `ollama serve`, then make sure the model configured in `OLLAMA_CHAT_MODEL` has been pulled.

```text
422 At least one user message is required
```

Send at least one message whose `role` is `"user"`.
