# University of KwaZulu-Natal Chat API

A FastAPI service that answers questions about the University of KwaZulu-Natal (UKZN) using retrieval-augmented generation (RAG). Pages from [ukzn.ac.za](https://ukzn.ac.za/) and [corporaterelations.ukzn.ac.za](https://corporaterelations.ukzn.ac.za/) are crawled, split into chunks, embedded with `nomic-embed-text`, and stored in PostgreSQL with pgvector. For each question, the API finds the most relevant chunks, gives them to a local Ollama chat model, and streams the answer back.

## Contents

- [How it works](#how-it-works)
- [Project layout](#project-layout)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Database](#database)
- [Building the knowledge base](#building-the-knowledge-base)
- [API](#api)
- [Answer behaviour and tuning](#answer-behaviour-and-tuning)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Security notes](#security-notes)
- [Known limitations](#known-limitations)

## How it works

```text
                 offline                                         per request
┌─────────────────────────────────────────┐   ┌──────────────────────────────────────────────┐
│ build_sitemap.py  crawl a site → URL list│   │ POST /chat                                   │
│ ingest.py         fetch → extract text   │   │  1. embed latest user message (search_query:)│
│                   → chunk → embed        │   │  2. nearest chunks from both tables          │
│                   (search_document:)     │   │  3. drop chunks with distance > 0.33         │
│                   → upsert into table    │   │     none left → fixed "couldn't find" reply  │
└──────────────────────┬──────────────────┘   │  4. chunks → system prompt → stream answer   │
                       ▼                      │                                              │
        PostgreSQL + pgvector                 └──────────────────────────────────────────────┘
        ├── corporaterelations  (corporaterelations.ukzn.ac.za)
        └── main_ukzn           (ukzn.ac.za)
```

- **Embeddings:** `nomic-embed-text` (768 dimensions) through Ollama. Documents are embedded with the `search_document: ` prefix and questions with `search_query: `, as the model expects.
- **Search:** cosine distance (`<=>`) with an HNSW index on each table. Both tables are searched and the overall nearest 5 chunks are kept.
- **Relevance cutoff:** chunks further than `MAX_SOURCE_DISTANCE` (0.33) are discarded. If nothing is left, the API replies with a fixed message and never calls the chat model, so off-topic questions are refused reliably.
- **Acronym handling:** "UKZN" in a question is replaced with "the University of KwaZulu-Natal" before embedding, because most pages spell the name out.
- **Small talk:** greetings and thanks get fixed replies before any search.

## Project layout

```text
main.py                  FastAPI app, lifespan (Ollama client + DB pool), GET /health
app/
  config.py              Settings loaded from .env / environment
  db.py                  PostgreSQL pool with pgvector adapters, get_conn dependency
  llm.py                 OllamaConnector: streaming chat, embeddings, installed-model check
  knowledge.py           Vector search across both tables, prompt context formatting
  chat.py                POST /chat, system prompt, small talk, relevance cutoff
scripts/
  build_sitemap.py       Breadth-first crawler that writes a URL list file
  ingest.py              Fetch, extract, chunk, embed and store pages from a URL list
sql/schema.sql           pgvector extension, both tables and their HNSW indexes
tests/                   Chunker tests and /chat behaviour tests (no DB or Ollama needed)
urls_*_simplescraper.txt URL lists used for ingestion
test_questions.txt       Manual test questions with expected top pages and distances
```

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com/) running locally (or reachable over the network)
- PostgreSQL 13+ with the [pgvector](https://github.com/pgvector/pgvector) extension (0.5+ for HNSW; developed against 0.8.7)
- `psql` (optional, for applying the schema and inspecting data)

Pull both models once:

```bash
ollama pull qwen2.5:1.5b       # chat model
ollama pull nomic-embed-text   # embedding model
```

If Ollama is not already running as an app or service, start it in a separate terminal:

```bash
ollama serve
```

## Quick start

```bash
# 1. Create and install the Python environment.
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Create your local configuration and set DATABASE_URL.
cp .env.example .env

# 3. Create the pgvector extension, tables and indexes (safe to re-run).
psql "$DATABASE_URL" -f sql/schema.sql

# 4. Load the knowledge base (see "Building the knowledge base").
.venv/bin/python -m scripts.ingest urls_corporaterelations_ukzn_ac_za_simplescraper.txt corporaterelations
.venv/bin/python -m scripts.ingest urls_ukzn_ac_za_simplescraper.txt main_ukzn

# 5. Start the API with auto-reload for development.
.venv/bin/fastapi dev main.py
```

`psql "$DATABASE_URL"` needs the variable in your shell. Either export it, or paste the connection string directly.

The API is available at `http://127.0.0.1:8000`. Interactive documentation is at `http://127.0.0.1:8000/docs`.

For production, use `fastapi run main.py` (no auto-reload) behind a reverse proxy.

## Configuration

Settings are read from `.env`; real environment variables take precedence. Values are validated at startup, so a missing or malformed `DATABASE_URL` stops the API from starting. Restart the API after changing `.env`. See `.env.example` for a commented template.

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Base URL of the Ollama server. |
| `OLLAMA_CHAT_MODEL` | `qwen2.5:1.5b` | Model that writes answers. |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Model that embeds questions and chunks. Must output 768-dimension vectors and match the model used at ingestion. |
| `DATABASE_URL` | (required) | PostgreSQL connection string, e.g. `postgresql://user:password@host:5432/dbname`. |
| `DB_POOL_MIN_SIZE` | `1` | Idle connections kept open by the API. |
| `DB_POOL_MAX_SIZE` | `5` | Maximum pooled connections, which caps concurrent `/chat` searches. |

### Changing the chat model

Small models answer quickly but follow instructions loosely; `qwen2.5:1.5b` sometimes ignores prompt rules, which is why off-topic refusals are enforced in code. A larger model improves answer quality:

```bash
ollama pull qwen2.5:7b
```

```dotenv
OLLAMA_CHAT_MODEL=qwen2.5:7b
```

### Changing the embedding model

The stored vectors only match questions embedded by the same model. If you change `OLLAMA_EMBED_MODEL`:

1. Update `vector(768)` in `sql/schema.sql` if the new model has a different dimension.
2. Drop and recreate the tables, then re-ingest both URL lists.
3. Re-measure `MAX_SOURCE_DISTANCE` (see [Answer behaviour and tuning](#answer-behaviour-and-tuning)), because distances differ between models.

## Database

`sql/schema.sql` is idempotent (`IF NOT EXISTS` throughout) and creates:

- the `vector` extension
- `corporaterelations`: chunks of corporaterelations.ukzn.ac.za pages
- `main_ukzn`: chunks of ukzn.ac.za pages

Both tables have the same shape, with one row per chunk:

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` | Primary key |
| `url` | `text` | Page URL (after redirects) |
| `title` | `text` | Page title |
| `chunk_index` | `int` | Position of the chunk in the page; `UNIQUE (url, chunk_index)` |
| `content` | `text` | Chunk text, prefixed with the page title |
| `content_hash` | `text` | sha256 of the page's full extracted text; unchanged pages are skipped on re-ingest |
| `embedding` | `vector(768)` | `nomic-embed-text` embedding, HNSW index with `vector_cosine_ops` |
| `scraped_at` | `timestamptz` | When the chunk was written |

Useful queries:

```sql
-- Pages and chunks per table
SELECT count(DISTINCT url) AS pages, count(*) AS chunks FROM corporaterelations;
SELECT count(DISTINCT url) AS pages, count(*) AS chunks FROM main_ukzn;

-- Everything stored for one page
SELECT chunk_index, left(content, 120) FROM main_ukzn
WHERE url = 'https://ukzn.ac.za/history/' ORDER BY chunk_index;

-- Remove one page (it will be re-added on the next ingest if still listed)
DELETE FROM main_ukzn WHERE url = 'https://ukzn.ac.za/some-page/';
```

## Building the knowledge base

Building the knowledge base takes two steps: make a URL list for a site, then ingest it into a table.

### 1. Crawl a site (optional)

The URL lists in the repository are ready to use. To regenerate one:

```bash
.venv/bin/python scripts/build_sitemap.py https://ukzn.ac.za/
```

- The crawl stays on the start URL's host (`www.` counts as the same host). Subdomains such as `law.ukzn.ac.za` need their own run.
- These are skipped: images, PDFs and other documents, CSS/JS, `wp-content`/`wp-json`/feeds, and URLs with query strings.
- Pagination, `/tag/` and `/category/` archive pages are followed to find links but not written, since they only repeat article excerpts.
- Transport errors are retried 5 times with exponential backoff (1, 2, 4, 8, 16 s). Pages that still fail are logged as `error <url>: gave up`.
- Output goes to `urls_<host>_simplescraper.txt` in the current directory, and progress is logged to stderr. A full ukzn.ac.za crawl takes roughly 30–60 minutes.

```bash
# Keep a log and check for pages that were not reached.
.venv/bin/python scripts/build_sitemap.py https://ukzn.ac.za/ 2> crawl.log
grep 'gave up' crawl.log
```

### 2. Ingest a URL list

Run from the project root (the `-m` form is required so `app` and `scripts` can be imported):

```bash
.venv/bin/python -m scripts.ingest <urls_file> <corporaterelations|main_ukzn>
```

For each URL, the script:

1. Downloads the page, with the same retries as the crawler.
2. Removes the site's Elementor header and footer (the mega-menu) and the skip link, then extracts the main text and title with trafilatura.
3. Skips pages with less than 200 characters of real content, deleting any rows stored for them earlier.
4. Skips pages whose text hash matches what is already stored. This makes re-runs cheap and lets an interrupted run resume where it stopped.
5. Splits the text into chunks of up to 1,500 characters, with 200 characters of overlap.
6. Embeds the chunks and replaces the page's rows in a single transaction.

Each page's outcome is logged to stderr (`ok`, `skip`, `retry`, `error`), followed by a summary:

```text
done: {'ingested': 2738, 'skipped': 37}; 8225 chunks in main_ukzn
```

Pages are processed one at a time, which keeps the load on UKZN's servers low. Expect about 1–2 seconds per page.

Only the two whitelisted table names are accepted. Adding a new site means adding a table to `sql/schema.sql`, its name to `TABLES` in `scripts/ingest.py`, and a branch to the query in `app/knowledge.py`.

### Keeping content fresh

Re-run the crawl and the ingest periodically, for example from a weekly cron job. Changed pages are re-embedded and unchanged ones are skipped. Pages removed from the website are **not** deleted automatically; remove them with SQL if needed.

## API

### `GET /health`

Confirms the API is running and reports configured Ollama models that are not installed:

```bash
curl http://127.0.0.1:8000/health
```

```json
{
  "status": "ok",
  "ollama_missing_models": []
}
```

If `ollama_missing_models` lists a model, run `ollama pull <model-name>`.

### `POST /chat`

Accepts the conversation history and streams back a plain-text answer (`text/plain; charset=utf-8`). The client keeps the history and sends it in full each time.

Request body:

| Field | Rules |
|---|---|
| `messages` | 1–50 items; at least one must have role `user` |
| `messages[].role` | `user` or `assistant` (`system` is not accepted; the server owns the system prompt) |
| `messages[].content` | 1–4,000 characters |

Only the **latest user message** is used to search the knowledge base. Earlier messages give the model conversational context.

Use `-N` with `curl` to see chunks as they arrive:

```bash
curl -N http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages": [{"role": "user", "content": "Who is the UKZN vice-chancellor?"}]}'
```

```text
The UKZN vice-chancellor is Professor Nana Poku.
```

A message that is only a greeting ("Hi", "Hello there", "Good morning", "Sawubona") or thanks ("Thanks!", "Ngiyabonga") gets a fixed welcome or "you're welcome" reply, without a search or a model call. A greeting followed by a question ("Hi, who is the vice-chancellor?") is answered normally.

When no stored chunk is close enough to the question (off-topic, or not on the website), the reply is fixed and the chat model is not called:

```text
I couldn't find that on the UKZN website. I can only help with questions about the University of KwaZulu-Natal (UKZN); for more information visit https://ukzn.ac.za/.
```

Follow-up message:

```bash
curl -N http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [
      {"role": "user", "content": "Who is the vice-chancellor?"},
      {"role": "assistant", "content": "The vice-chancellor is Professor Nana Poku."},
      {"role": "user", "content": "What does the vice-chancellor of UKZN do?"}
    ]
  }'
```

Error responses:

| Status | Detail | Cause |
|---|---|---|
| `422` | validation details | Body does not match the rules above |
| `422` | `At least one user message is required` | No message with role `user` |
| `502` | `LLM unavailable` | Ollama is unreachable, or a model is not installed |
| `503` | `Knowledge base unavailable` | The database query failed |

Errors are returned before streaming starts, so a client never receives a half-finished answer followed by an error.

### Python client example

```python
import httpx

payload = {"messages": [{"role": "user", "content": "What does the Media Relations unit do?"}]}

with httpx.stream("POST", "http://127.0.0.1:8000/chat", json=payload, timeout=120) as response:
    response.raise_for_status()
    for text in response.iter_text():
        print(text, end="", flush=True)
```

## Answer behaviour and tuning

These values live in code:

| Setting | Location | Value | Effect |
|---|---|---|---|
| `SYSTEM_PROMPT` | `app/chat.py` | — | Assistant role and rules. Sources come before the rules because small models follow the most recent instructions best. |
| `MAX_SOURCE_DISTANCE` | `app/chat.py` | `0.33` | Chunks further than this are ignored. If none remain, the fixed reply is sent. |
| `NO_MATCH_REPLY` | `app/chat.py` | — | The fixed reply for off-topic or unanswerable questions. |
| `DEFAULT_LIMIT` | `app/knowledge.py` | `5` | Chunks retrieved per question, before the cutoff. |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `scripts/ingest.py` | `1500` / `200` | Chunk length and overlap, in characters. Re-ingest after changing them. |
| `MIN_CONTENT_CHARS` | `scripts/ingest.py` | `200` | Pages with less main text are not stored. |

### Tuning the relevance cutoff

The cutoff was measured on the full data. UKZN questions had best-match distances of 0.14–0.31, and off-topic questions 0.35–0.47.

- **Raise it** if real UKZN questions get the fixed reply.
- **Lower it** if off-topic questions get answered.

Re-check it after re-ingesting, changing the embedding model, or adding a table. Use the questions in `test_questions.txt`, which lists the expected source page and distance for each.

Questions that use "UKZN" have it replaced with "the University of KwaZulu-Natal" before searching. Most pages spell the name out, and keeping the acronym makes questions match generic UKZN overview pages instead of the page with the answer. For example, "When was UKZN formed?" ranked `/history/` 6th with the acronym kept, and 1st with it replaced.

## Testing

```bash
.venv/bin/python -m pytest
```

Use `python -m pytest` rather than `pytest`, so the project root is importable. The tests need neither a database nor Ollama:

- `tests/test_ingest.py`: chunk size limits, overlap, and empty or short text.
- `tests/test_chat.py`: the fixed reply for distant matches (with the model not called), the model's answer streamed unchanged, only chunks within the cutoff reaching the prompt, and greeting/thanks detection.

For a manual end-to-end check, start the API and send the questions in `test_questions.txt`. The first two groups should be answered, and the last group should get the fixed reply.

## Troubleshooting

| Symptom | Fix |
|---|---|
| API fails at startup with a validation error | `DATABASE_URL` is missing or malformed in `.env`. |
| API fails at startup with a connection error | The database is unreachable; check the host, port, credentials and firewall. |
| `502 LLM unavailable` | Start Ollama (`ollama serve`), and pull any models listed by `/health`. |
| `503 Knowledge base unavailable` | The database went away, or the tables don't exist; apply `sql/schema.sql`. |
| Every question gets the fixed reply | The tables are empty (run the ingest), or the embedding model differs from the one used at ingestion. |
| `ModuleNotFoundError: No module named 'app'` or `'scripts'` | Run scripts and tests with `python -m` from the project root. |
| `type "vector" does not exist` | pgvector isn't installed on the database server, or the role can't create extensions. |
| Ingest logs many `retry` lines | Network trouble, or the website is throttling requests; re-run the ingest and it will resume. |

## Security notes

- **Secrets:** `.env` holds the database credentials and is git-ignored. Never commit it. Use a database role with access to only these tables.
- **System prompt:** clients cannot send `system` messages, so they can't replace the server's prompt. Retrieved page text is labelled as reference material, not instructions.
- **SQL injection:** all queries use bound parameters. Table names in the ingest script come from a fixed whitelist and are quoted with `psycopg.sql.Identifier`.
- **Input limits:** requests are capped at 50 messages of 4,000 characters each.
- **No authentication or rate limiting:** every `/chat` request runs an embedding and a model generation, which is expensive. Before exposing the API publicly, add authentication and rate limiting, for example at a reverse proxy, and restrict CORS to the site that embeds the chat.

## Known limitations

- **Made-up answers when the right page isn't retrieved:** if the page with the answer doesn't reach the top 5, the model can invent an answer from loosely related pages. General overview pages (`/`, `/general/welcome-to-ukzn/`, `/zulu/`) still outrank more specific pages for broad questions such as "UKZN annual reports" or "What is UKZN Ndaba Online?".
- **Outdated content:** answers can come from old pages. For example, registration questions match 2018 registration notices, because nothing marks which content is current.
- **Follow-up questions:** only the latest user message is searched, so a follow-up like "what's their phone number?" may retrieve the wrong pages.
- **Not covered:** PDFs, other UKZN subdomains, and pages that render their content with JavaScript.
- **Deleted pages:** pages removed from the website stay in the database until deleted manually.
- **Small model:** `qwen2.5:1.5b` can still paraphrase loosely or miss details; a larger chat model gives better answers.
