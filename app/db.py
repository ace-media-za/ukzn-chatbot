"""Legacy PostgreSQL helpers retained for reference.

The direct-Ollama application does not import this module or open a database
connection. It can be removed if vector-search support will not return.
"""

from collections.abc import AsyncIterator

from fastapi import Request
from pgvector.psycopg import register_vector_async
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.config import Settings


async def _configure(conn: AsyncConnection) -> None:
    """Register pgvector adapters for a newly opened asynchronous connection."""

    await register_vector_async(conn)


def create_pool(settings: Settings) -> AsyncConnectionPool:
    """Create a closed PostgreSQL pool for the former retrieval workflow."""

    return AsyncConnectionPool(
        conninfo=str(settings.database_url),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        configure=_configure,
        open=False,
    )


async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    """Yield a pooled PostgreSQL connection for a request in retrieval mode."""

    pool: AsyncConnectionPool = request.app.state.pool
    async with pool.connection() as conn:
        yield conn
