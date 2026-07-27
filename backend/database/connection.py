import asyncpg
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_db() first")
    return _pool


async def init_db(database_url: str) -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        database_url,
        min_size=2,
        max_size=10,
        timeout=10,
        command_timeout=60,
    )
    schema = Path(__file__).parent / "schema.sql"
    async with _pool.acquire() as conn:
        await conn.execute(schema.read_text(encoding="utf-8"))
    logger.info("Database initialised")


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
