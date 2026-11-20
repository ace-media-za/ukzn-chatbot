"""PostgreSQL connection pool and FastAPI dependency for knowledge-base search."""

from collections.abc import AsyncIterator

from fastapi import Request
from pgvector.psycopg import register_vector_async
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.config import Settings


async def _configure(conn: AsyncConnection) -> None:
    """Register pgvector type adapters on a newly opened pooled connection.

    Args:
        conn: Connection just opened by the pool.
    """

    await register_vector_async(conn)


def create_pool(settings: Settings) -> AsyncConnectionPool:
    """Create the application's PostgreSQL connection pool without opening it.

    The pool is opened and closed by the FastAPI lifespan in ``main.py``.
    Every connection is configured with pgvector adapters.

    Args:
        settings: Application settings providing the DSN and pool sizes.

    Returns:
        An unopened asynchronous connection pool.
    """

    return AsyncConnectionPool(
        conninfo=str(settings.database_url),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        configure=_configure,
        open=False,
    )


async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    """FastAPI dependency that yields a pooled PostgreSQL connection.

    The connection is returned to the pool when the request's dependency
    scope ends.

    Args:
        request: Current request, used to reach the application-scoped pool.

    Yields:
        A connection configured with pgvector adapters.
    """

    pool: AsyncConnectionPool = request.app.state.pool
    async with pool.connection() as conn:
        yield conn
