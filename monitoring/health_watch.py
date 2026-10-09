"""Independent /health watcher. Run outside the Sifox API container.

Required environment: HEALTH_URL, MM_SERVER_URL, MM_TOKEN, ALERT_MM_USERNAME.
One alert follows three failed probes; one recovery follows two good probes.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class HealthState:
    def __init__(self):
        self.failures = 0
        self.successes = 0
        self.alerted = False

    def observe(self, healthy: bool) -> str | None:
        if healthy:
            self.failures = 0
            self.successes += 1
            if self.alerted and self.successes >= 2:
                self.alerted = False
                return "recovered"
        else:
            self.successes = 0
            self.failures += 1
            if not self.alerted and self.failures >= 3:
                self.alerted = True
                return "down"
        return None


def _json_request(url: str, *, token: str, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def send_dm(base: str, token: str, username: str, message: str) -> None:
    base = base.rstrip("/")
    me = _json_request(f"{base}/api/v4/users/me", token=token)
    target = _json_request(
        f"{base}/api/v4/users/username/{urllib.parse.quote(username, safe='')}",
        token=token,
    )
    if me["id"] == target["id"]:
        raise ValueError("Mattermost alert sender must differ from recipient")
    channel = _json_request(
        f"{base}/api/v4/channels/direct", token=token,
        payload=[me["id"], target["id"]],
    )
    _json_request(
        f"{base}/api/v4/posts", token=token,
        payload={"channel_id": channel["id"], "message": message},
    )


def probe(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            return response.status == 200
    except (OSError, urllib.error.HTTPError):
        return False


def main() -> None:
    names = ("HEALTH_URL", "MM_SERVER_URL", "MM_TOKEN", "ALERT_MM_USERNAME")
    settings = {name: os.environ.get(name, "").strip() for name in names}
    if not all(settings.values()):
        raise SystemExit("Missing required monitoring configuration")
    state = HealthState()
    while True:
        event = state.observe(probe(settings["HEALTH_URL"]))
        if event:
            message = (
                "🔴 Sifox: сервис не отвечает на проверку /health (3 проверки подряд)."
                if event == "down" else
                "🟢 Sifox: сервис снова отвечает на проверку /health."
            )
            try:
                send_dm(
                    settings["MM_SERVER_URL"], settings["MM_TOKEN"],
                    settings["ALERT_MM_USERNAME"], message,
                )
            except Exception as exc:
                # Avoid leaking access tokens or server response bodies in logs.
                print(f"Health notification failed: {type(exc).__name__}", flush=True)
                state.alerted = event != "down"
        time.sleep(60)


if __name__ == "__main__":
    main()
