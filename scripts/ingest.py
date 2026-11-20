"""Fetch, chunk and embed the pages in a URL list into a knowledge-base table.

For each URL the script downloads the page, strips site navigation, extracts
the main text with trafilatura, splits it into overlapping chunks, embeds them
with ``nomic-embed-text`` and replaces that page's rows in the table in one
transaction. Pages whose text is unchanged since the last run (same sha256)
are skipped, so re-runs are cheap and an interrupted run can be resumed.
Pages with no real content have any existing rows deleted. Progress and a
final summary are logged to stderr.

Requires the tables from ``sql/schema.sql`` and the settings in ``.env``.

Usage (from the project root): .venv/bin/python -m scripts.ingest <urls_file> <corporaterelations|main_ukzn>
"""

import asyncio
import hashlib
import sys
from collections import Counter
from pathlib import Path

import httpx
import lxml.html
import trafilatura
from pgvector.psycopg import register_vector_async
from psycopg import AsyncConnection, sql

from app.config import get_settings
from app.knowledge import DOCUMENT_PREFIX
from app.llm import OllamaConnector
from scripts.build_sitemap import fetch

# Whitelist: table names are SQL identifiers and cannot be bound as query parameters.
TABLES = ("corporaterelations", "main_ukzn")
CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200
MIN_CONTENT_CHARS = 200
# Elementor's site-wide mega-menu/footer templates; trafilatura otherwise falls back to them on thin pages.
BOILERPLATE_XPATH = '//*[@data-elementor-type="header" or @data-elementor-type="footer"] | //a[contains(@class, "skip-link")]'


def extract(html: str, url: str) -> tuple[str, str] | None:
    """Extract a page's title and main text with site navigation removed.

    Args:
        html: Raw page HTML.
        url: Page URL, used as the title when the page has none.

    Returns:
        ``(title, text)``, or None when fewer than ``MIN_CONTENT_CHARS``
        characters of main content remain.
    """

    tree = lxml.html.fromstring(html)
    for element in tree.xpath(BOILERPLATE_XPATH):
        element.drop_tree()
    document = trafilatura.bare_extraction(tree, url=url, with_metadata=True)
    if not document or not document.text or len(document.text) < MIN_CONTENT_CHARS:
        return None
    return (document.title or url).strip(), document.text


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks along paragraph boundaries.

    Lines are packed greedily; a paragraph longer than the limit is cut into
    pieces. Each chunk after the first starts with the last ``overlap``
    characters of the previous chunk, so facts spanning a boundary appear
    whole in at least one chunk.

    Args:
        text: Plain text with one paragraph per line.
        size: Maximum characters per chunk.
        overlap: Characters carried over from the previous chunk.

    Returns:
        Chunks in document order; empty when ``text`` has no content.
    """

    # Pieces are capped so tail + newline + piece always fits within size.
    piece_max = size - overlap - 1
    pieces = [
        paragraph[i : i + piece_max]
        for paragraph in (line.strip() for line in text.splitlines())
        if paragraph
        for i in range(0, len(paragraph), piece_max)
    ]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) > size:
            chunks.append(current)
            current = current[-overlap:]
        current = f"{current}\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


async def stored_hash(conn: AsyncConnection, table: sql.Identifier, url: str) -> str | None:
    """Return the content hash stored for ``url`` in ``table``, or None if the page is not stored."""

    cur = await conn.execute(sql.SQL("SELECT content_hash FROM {} WHERE url = %s LIMIT 1").format(table), (url,))
    row = await cur.fetchone()
    return row[0] if row else None


async def replace_page(
    conn: AsyncConnection,
    table: sql.Identifier,
    url: str,
    title: str,
    content_hash: str,
    chunks: list[str],
    embeddings: list[list[float]],
) -> None:
    """Replace all of a page's chunks in one transaction.

    Deleting before inserting means a page that shrank leaves no stale rows,
    and readers never see a half-written page.

    Args:
        conn: Database connection in autocommit mode.
        table: Whitelisted target table.
        url: Page URL.
        title: Page title.
        content_hash: sha256 of the page's full extracted text.
        chunks: Chunk texts in document order.
        embeddings: One embedding per chunk, in the same order.
    """

    async with conn.transaction():
        await conn.execute(sql.SQL("DELETE FROM {} WHERE url = %s").format(table), (url,))
        async with conn.cursor() as cur:
            await cur.executemany(
                sql.SQL(
                    "INSERT INTO {} (url, title, chunk_index, content, content_hash, embedding)"
                    " VALUES (%s, %s, %s, %s, %s, %s::vector)"
                ).format(table),
                [(url, title, i, c, content_hash, e) for i, (c, e) in enumerate(zip(chunks, embeddings, strict=True))],
            )


async def ingest_page(
    conn: AsyncConnection, table: sql.Identifier, llm: OllamaConnector, http: httpx.Client, url: str
) -> str:
    """Fetch, extract, chunk, embed and store one page.

    Args:
        conn: Database connection in autocommit mode.
        table: Whitelisted target table.
        llm: Connector used to embed the chunks.
        http: HTTP client used to fetch the page.
        url: Page URL.

    Returns:
        One of ``"ingested"``, ``"unchanged"``, ``"skipped"`` or ``"failed"``.
    """

    response = fetch(http, url)
    if response is None:
        return "failed"
    if response.status_code != 200 or "text/html" not in response.headers.get("content-type", ""):
        print(f"skip {url}: HTTP {response.status_code}", file=sys.stderr)
        return "skipped"

    extracted = extract(response.text, url)
    if extracted is None:
        await conn.execute(sql.SQL("DELETE FROM {} WHERE url = %s").format(table), (url,))
        print(f"skip {url}: no main content", file=sys.stderr)
        return "skipped"
    title, text = extracted

    content_hash = hashlib.sha256(text.encode()).hexdigest()
    if await stored_hash(conn, table, url) == content_hash:
        return "unchanged"

    chunks = [f"{title}\n\n{chunk}" for chunk in chunk_text(text)]
    embeddings = await llm.embed([DOCUMENT_PREFIX + chunk for chunk in chunks])
    await replace_page(conn, table, url, title, content_hash, chunks, embeddings)
    print(f"ok {url}: {len(chunks)} chunks", file=sys.stderr)
    return "ingested"


async def main(urls_file: Path, table_name: str) -> None:
    """Ingest every URL in ``urls_file`` into ``table_name`` sequentially.

    Args:
        urls_file: Text file with one URL per line; blank and duplicate lines
            are ignored.
        table_name: One of ``TABLES``.

    Raises:
        SystemExit: If ``table_name`` is not whitelisted.
    """

    if table_name not in TABLES:
        raise SystemExit(f"table must be one of {TABLES}")
    table = sql.Identifier(table_name)
    urls = list(dict.fromkeys(line.strip() for line in urls_file.read_text().splitlines() if line.strip()))
    settings = get_settings()
    llm = OllamaConnector(settings)
    outcomes: Counter[str] = Counter()
    try:
        async with await AsyncConnection.connect(str(settings.database_url), autocommit=True) as conn:
            await register_vector_async(conn)
            with httpx.Client(follow_redirects=True, timeout=20, headers={"User-Agent": "ukzn-ingest/1.0"}) as http:
                for url in urls:
                    outcomes[await ingest_page(conn, table, llm, http, url)] += 1
            cur = await conn.execute(sql.SQL("SELECT count(*) FROM {}").format(table))
            total_chunks = (await cur.fetchone() or (0,))[0]
    finally:
        await llm.close()
    print(f"done: {dict(outcomes)}; {total_chunks} chunks in {table_name}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
