"""Private Mattermost delivery for operational alerts."""
from urllib.parse import quote

import httpx


async def send_direct_message(
    server_url: str, token: str, username: str, message: str,
) -> None:
    """Post one DM; raise on delivery failure so the outbox can retry."""
    base = server_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
        me = await client.get(f"{base}/api/v4/users/me")
        me.raise_for_status()
        target = await client.get(f"{base}/api/v4/users/username/{quote(username, safe='')}")
        target.raise_for_status()
        me_id = me.json()["id"]
        target_id = target.json()["id"]
        if me_id == target_id:
            raise ValueError("Alert token belongs to the recipient; use a separate bot account")
        channel = await client.post(
            f"{base}/api/v4/channels/direct", json=[me_id, target_id],
        )
        channel.raise_for_status()
        post = await client.post(
            f"{base}/api/v4/posts",
            json={"channel_id": channel.json()["id"], "message": message},
        )
        post.raise_for_status()
