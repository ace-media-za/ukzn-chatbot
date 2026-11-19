"""Legacy vector-search helpers for the former CMS retrieval workflow.

The direct-Ollama application does not import this module. It is retained only
as a reference for a future retrieval implementation.
"""

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel

from app.llm import OllamaConnector

# nomic-embed-text is trained with these task prefixes
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "
DEFAULT_LIMIT = 5


class SearchResult(BaseModel):
    """A single knowledge-base chunk returned by vector similarity search."""

    title: str
    slug: str | None
    content: str


async def search(conn: AsyncConnection, embedding: list[float], limit: int = DEFAULT_LIMIT) -> list[SearchResult]:
    """Return the nearest stored chunks for a query embedding.

    Args:
        conn: PostgreSQL connection configured with pgvector adapters.
        embedding: Query vector in the database embedding dimension.
        limit: Maximum number of chunks to return.
    """

    async with conn.cursor(row_factory=class_row(SearchResult)) as cur:
        await cur.execute(
            "SELECT title, slug, content FROM chunks ORDER BY embedding <=> %s::vector LIMIT %s",
            (embedding, limit),
        )
        return await cur.fetchall()


async def retrieve(conn: AsyncConnection, llm: OllamaConnector, question: str) -> list[SearchResult]:
    """Embed ``question`` and find its most similar stored knowledge chunks."""

    [embedding] = await llm.embed([QUERY_PREFIX + question])
    return await search(conn, embedding)
