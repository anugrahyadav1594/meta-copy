"""CLI health check against a running MetaScale API (exit code 0 when ready)."""

from __future__ import annotations

import json
import os
import sys

import httpx

BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")


def main() -> int:
    try:
        health = httpx.get(f"{BASE}/health", timeout=5)
        ready = httpx.get(f"{BASE}/ready", timeout=15)
    except httpx.HTTPError as exc:
        print(f"UNREACHABLE: {exc}")
        return 1
    print("/health", health.status_code, json.dumps(health.json()))
    print("/ready ", ready.status_code, json.dumps(ready.json()))
    if health.status_code != 200:
        return 1
    if ready.status_code != 200 or ready.json().get("status") != "ready":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
