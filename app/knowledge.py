"""Vector search over the ingested corporaterelations.ukzn.ac.za and ukzn.ac.za pages.

Both tables store one row per text chunk with a 768-dimension
``nomic-embed-text`` embedding and an HNSW cosine index (see
``sql/schema.sql``). Lower cosine distance means more similar.
"""

import re
from collections.abc import Sequence

from pydantic import BaseModel
from psycopg.rows import class_row
from psycopg import AsyncConnection

from app.llm import OllamaConnector

# nomic-embed-text is trained with these task prefixes
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "
DEFAULT_LIMIT = 5


class SearchResult(BaseModel):
    """A single knowledge-base chunk returned by vector similarity search.

    Attributes:
        title: Title of the page the chunk came from.
        url: Canonical URL of that page; shown to users as a source.
        content: Chunk text, prefixed with the page title.
        distance: Cosine distance to the query (0 is identical, 2 is opposite).
    """

    title: str
    url: str
    content: str
    distance: float


async def search(conn: AsyncConnection, embedding: list[float], limit: int = DEFAULT_LIMIT) -> list[SearchResult]:
    """Return the nearest stored chunks for a query embedding.

    Args:
        conn: PostgreSQL connection configured with pgvector adapters.
        embedding: Query vector in the database embedding dimension.
        limit: Maximum number of chunks to return.

    Returns:
        Up to ``limit`` chunks from either table, nearest first.
    """

    # Per-table ORDER BY/LIMIT lets each HNSW index serve its branch before the merge.
    query = """
        SELECT title, url, content, distance FROM (
            (SELECT title, url, content, embedding <=> %(q)s::vector AS distance
             FROM corporaterelations ORDER BY distance LIMIT %(n)s)
            UNION ALL
            (SELECT title, url, content, embedding <=> %(q)s::vector AS distance
             FROM main_ukzn ORDER BY distance LIMIT %(n)s)
        ) AS nearest
        ORDER BY distance
        LIMIT %(n)s
    """
    async with conn.cursor(row_factory=class_row(SearchResult)) as cur:
        await cur.execute(query, {"q": embedding, "n": limit})
        return await cur.fetchall()


async def retrieve(conn: AsyncConnection, llm: OllamaConnector, question: str) -> list[SearchResult]:
    """Embed ``question`` and find its most similar stored knowledge chunks.

    "UKZN" is replaced with the full university name before embedding. Most
    pages spell the name out, and keeping the acronym makes the query match
    generic UKZN overview pages instead of the page that answers it.

    Args:
        conn: PostgreSQL connection configured with pgvector adapters.
        llm: Connector used to embed the question.
        question: The user's question as typed.

    Returns:
        The nearest chunks, nearest first, including their distances so the
        caller can apply a relevance cutoff.
    """

    # Replace rather than append: "UKZN (University of KwaZulu-Natal)" ranked /history/ 6th for
    # "When was UKZN formed?"; the full name alone ranks it 1st.
    expanded = re.sub(r"\b(the\s+)?UKZN\b", "the University of KwaZulu-Natal", question, flags=re.IGNORECASE)
    [embedding] = await llm.embed([QUERY_PREFIX + expanded])
    return await search(conn, embedding)


def format_context(results: Sequence[SearchResult]) -> str:
    """Render retrieved chunks as numbered sources for the system prompt.

    Args:
        results: Chunks to include, in the order they should be numbered.

    Returns:
        Blocks of ``[n] title (url)`` followed by the chunk text, separated by
        blank lines.
    """

    return "\n\n".join(f"[{i}] {r.title} ({r.url})\n{r.content}" for i, r in enumerate(results, start=1))
