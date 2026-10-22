"""One-time, staging-only rotation of encrypted Google OAuth tokens.

Run inside the staging Railway service after the dual-key release is healthy:
    python rotate_encryption_key.py             # read-only check
    python rotate_encryption_key.py --apply     # atomic re-encryption
    python rotate_encryption_key.py             # confirm old-key count is zero

ENCRYPTION_KEY is the old key, ENCRYPTION_KEY_NEXT is the new key, and
ENCRYPTION_WRITE_NEXT must already be true. Do not replace ENCRYPTION_KEY
until the final check reports zero old-key tokens.
"""

import argparse
import asyncio
import os
from urllib.parse import urlsplit

from config import config
from utils.encryption import rotate_encrypted_token, validate_encryption_keys


_TOKEN_TABLES = ("google_tokens", "gmail_send_tokens")


def ensure_staging_target() -> None:
    """Keep rotation diagnostics and writes inside Railway staging only."""
    if config.APP_ENV != "staging" or os.getenv("RAILWAY_ENVIRONMENT_NAME") != "staging":
        raise RuntimeError("Rotation may only run inside the Railway staging environment")
    if not (urlsplit(config.DATABASE_URL).hostname or "").endswith(".railway.internal"):
        raise RuntimeError("Rotation requires the private staging database")
    if not config.ENCRYPTION_KEY_NEXT:
        raise RuntimeError("ENCRYPTION_KEY_NEXT must be configured first")
    validate_encryption_keys()


async def rotate_stored_tokens(conn, *, apply: bool) -> dict[str, tuple[int, int]]:
    """Inspect or rotate both token tables; fail atomically on bad ciphertext."""
    result = {}
    async with conn.transaction():
        for table in _TOKEN_TABLES:
            lock = " FOR UPDATE" if apply else ""
            rows = await conn.fetch(
                f"SELECT user_id, access_token, refresh_token FROM {table}{lock}"
            )
            old_count = 0
            for row in rows:
                access, access_changed = rotate_encrypted_token(row["access_token"])
                refresh = row["refresh_token"]
                refresh_changed = False
                if refresh is not None:
                    refresh, refresh_changed = rotate_encrypted_token(refresh)
                old_count += int(access_changed) + int(refresh_changed)
                if apply and (access_changed or refresh_changed):
                    await conn.execute(
                        f"UPDATE {table} SET access_token = $1, refresh_token = $2 "
                        "WHERE user_id = $3",
                        access, refresh, row["user_id"],
                    )
            result[table] = (len(rows), old_count)
    return result


async def _main(*, apply: bool) -> None:
    ensure_staging_target()
    if apply and not config.ENCRYPTION_WRITE_NEXT:
        raise RuntimeError("Enable ENCRYPTION_WRITE_NEXT before applying rotation")

    import asyncpg
    conn = await asyncpg.connect(config.DATABASE_URL, timeout=10)
    try:
        result = await rotate_stored_tokens(conn, apply=apply)
    finally:
        await conn.close()
    for table, (rows, old_tokens) in result.items():
        print(f"{table}: {rows} rows; {old_tokens} old-key token fields")
    print("Rotation applied" if apply else "Read-only check complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Re-encrypt old-key tokens in staging")
    args = parser.parse_args()
    asyncio.run(_main(apply=args.apply))
