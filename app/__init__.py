"""UKZN retrieval-augmented chat API.

Request flow for ``POST /chat``:

1. ``app.chat`` validates the conversation and takes the latest user message.
2. ``app.knowledge`` embeds it through ``app.llm`` (``nomic-embed-text``) and
   searches the ``corporaterelations`` and ``main_ukzn`` pgvector tables via a
   pooled connection from ``app.db``.
3. Chunks within the distance cutoff are injected into the system prompt, the
   chat model's answer is streamed back, and the source URLs are appended.

Configuration is loaded once by ``app.config``. The tables are populated
offline by ``scripts/build_sitemap.py`` and ``scripts/ingest.py``.
"""
