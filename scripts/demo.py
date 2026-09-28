#!/usr/bin/env python3
"""The 20-step MetaScale demo, executed against a RUNNING API.

Nothing in this script is pre-recorded: every value printed is fetched from
the live system. If a step fails, the script stops and says so.

    python scripts/demo.py                       # http://localhost:8000
    python scripts/demo.py --base-url http://host:8000
    python scripts/demo.py --pause 1.5           # slower, for a projector

The matching narrative is docs/DEMO.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

STEP_WIDTH = 78


def _request(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    form: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[int, dict[str, str], Any]:
    data = None
    headers = {"accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["content-type"] = content_type
    if form is not None:
        data = form
        headers["content-type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            head = dict(response.headers)
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                body = raw.decode(errors="replace")
            return response.status, head, body
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a body
        raw = exc.read()
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw.decode(errors="replace")
        return exc.code, dict(exc.headers or {}), body


class Demo:
    def __init__(self, base: str, pause: float) -> None:
        self.base = base.rstrip("/")
        self.pause = pause
        self.step = 0
        self.ids: dict[str, Any] = {}

    # ------------------------------------------------------------------ utils
    def call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        expect: tuple[int, ...] = (200, 201, 204),
        form: bytes | None = None,
        content_type: str = "application/json",
    ) -> Any:
        url = f"{self.base}{path}"
        status, head, body = _request(method, url, payload, form, content_type)
        if expect and status not in expect:
            raise SystemExit(f"\n  !! {method} {path} -> {status}: {body}\n")
        self._last_headers = head
        return body

    def title(self, text: str) -> None:
        self.step += 1
        print()
        print("=" * STEP_WIDTH)
        print(f"STEP {self.step:>2}. {text}")
        print("=" * STEP_WIDTH)

    def show(self, label: str, value: Any) -> None:
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, indent=2, default=str)
            if len(rendered) > 900:
                rendered = rendered[:900] + "\n  ... (truncated)"
            print(f"  {label}:")
            for line in rendered.splitlines():
                print(f"    {line}")
        else:
            print(f"  {label}: {value}")

    def note(self, text: str) -> None:
        print(f"  note: {text}")

    # ------------------------------------------------------------------ steps
    def run(self) -> int:
        print("MetaScale live demo — every number below is read from the running system")
        print(f"base url: {self.base}")

        # 1 ------------------------------------------------------------------
        self.title("Liveness: is the process up?")
        self.show("GET /health", self.call("GET", "/health"))
        time.sleep(self.pause)

        # 2 ------------------------------------------------------------------
        self.title("Readiness: which layers are active, which deps are reachable?")
        ready = self.call("GET", "/ready")
        self.show("mode", ready["mode"])
        self.show("derived systems", ready["derived_systems"])
        self.show("dependency checks", ready["checks"])
        self.note(
            "optional backends report 'disabled' and never fail the app; "
            "PostgreSQL is the only required dependency"
        )
        time.sleep(self.pause)

        # 3 ------------------------------------------------------------------
        self.title("Create two users (writes go to PostgreSQL, never to a cache)")
        stamp = str(int(time.time()))
        alice = self.call(
            "POST",
            "/api/v1/users",
            {
                "username": f"demo_alice_{stamp}",
                "email": f"demo_alice_{stamp}@example.com",
                "password": "password123",
                "display_name": "Demo Alice",
            },
            expect=(201,),
        )
        bob = self.call(
            "POST",
            "/api/v1/users",
            {
                "username": f"demo_bob_{stamp}",
                "email": f"demo_bob_{stamp}@example.com",
                "password": "password123",
                "display_name": "Demo Bob",
            },
            expect=(201,),
        )
        self.ids["alice"] = alice["user_id"]
        self.ids["bob"] = bob["user_id"]
        self.show("alice", alice["user_id"])
        self.show("bob", bob["user_id"])
        time.sleep(self.pause)

        # 4 ------------------------------------------------------------------
        self.title("Shard routing: which cluster owns this user, and why?")
        route = self.call("GET", f"/api/v1/shards/route/user/{self.ids['alice']}")
        self.show("route", route)
        self.note("user_id is the shard key; the ring uses consistent hashing + vnodes")
        time.sleep(self.pause)

        # 5 ------------------------------------------------------------------
        self.title("Create a post (and extract its hashtags into the 3NF vocabulary)")
        post = self.call(
            "POST",
            "/api/v1/posts",
            {
                "user_id": self.ids["alice"],
                "content": "Distributed sharding with a rebuildable cache #metascale #demo",
                "visibility": "public",
            },
            expect=(201,),
        )
        self.ids["post"] = post["post_id"]
        self.show("post_id", post["post_id"])
        self.show("hashtags", self.call("GET", f"/api/v1/posts/{post['post_id']}/hashtags"))
        time.sleep(self.pause)

        # 6 ------------------------------------------------------------------
        self.title("Read the post: cache MISS, the row is loaded from PostgreSQL")
        first = self.call("GET", f"/api/v1/posts/{self.ids['post']}")
        first_cache = self._last_headers.get("x-cache", "(header absent)")
        self.show("x-cache", first_cache)
        self.show("content", first["content"])
        time.sleep(self.pause)

        # 7 ------------------------------------------------------------------
        self.title("Read it again: cache HIT, PostgreSQL is not touched")
        second = self.call("GET", f"/api/v1/posts/{self.ids['post']}")
        second_cache = self._last_headers.get("x-cache", "(header absent)")
        self.show("x-cache", second_cache)
        self.show("payload identical to the MISS response", first == second)
        time.sleep(self.pause)

        # 8 ------------------------------------------------------------------
        self.title("Cache metrics: measured hits, misses, hit ratio, hot keys")
        self.show("GET /api/v1/cache/metrics", self.call("GET", "/api/v1/cache/metrics"))
        time.sleep(self.pause)

        # 9 ------------------------------------------------------------------
        self.title("Update the post: the write invalidates the cached copy")
        self.call(
            "PATCH",
            f"/api/v1/posts/{self.ids['post']}",
            {"content": "Edited: distributed sharding with a rebuildable cache #metascale"},
        )
        after = self.call("GET", f"/api/v1/posts/{self.ids['post']}")
        self.show("x-cache after write", self._last_headers.get("x-cache"))
        self.show("new content", after["content"])
        time.sleep(self.pause)

        # 10 ------------------------------------------------------------------
        self.title("Comment on the post")
        comment = self.call(
            "POST",
            f"/api/v1/posts/{self.ids['post']}/comments",
            {"user_id": self.ids["bob"], "content": "first comment"},
            expect=(201,),
        )
        self.show("comment_id", comment["comment_id"])
        time.sleep(self.pause)

        # 11 ------------------------------------------------------------------
        self.title("Like the post twice: the UNIQUE edge makes it idempotent")
        like1 = self.call(
            "POST",
            f"/api/v1/posts/{self.ids['post']}/like",
            {"user_id": self.ids["bob"]},
            expect=(201,),
        )
        like2 = self.call(
            "POST",
            f"/api/v1/posts/{self.ids['post']}/like",
            {"user_id": self.ids["bob"]},
            expect=(201,),
        )
        self.show("like_count after 1st like", like1["like_count"])
        self.show("like_count after 2nd like", like2["like_count"])
        time.sleep(self.pause)

        # 12 ------------------------------------------------------------------
        self.title("Read the counters back from PostgreSQL")
        self.show("likes", self.call("GET", f"/api/v1/posts/{self.ids['post']}/likes"))
        self.show("comments", self.call("GET", f"/api/v1/posts/{self.ids['post']}/comments"))
        time.sleep(self.pause)

        # 13 ------------------------------------------------------------------
        self.title("Follow relationships: plain canonical rows (no separate graph store)")
        cara = self.call(
            "POST",
            "/api/v1/users",
            {
                "username": f"demo_cara_{stamp}",
                "email": f"demo_cara_{stamp}@example.com",
                "password": "password123",
            },
            expect=(201,),
        )
        self.ids["cara"] = cara["user_id"]
        follow = self.call(
            "POST",
            f"/api/v1/users/{self.ids['bob']}/follow",
            {"following_id": self.ids["alice"]},
            expect=(201,),
        )
        self.call(
            "POST",
            f"/api/v1/users/{self.ids['cara']}/follow",
            {"following_id": self.ids["alice"]},
            expect=(201,),
        )
        self.show("bob -> alice", follow)
        self.note("re-running the same follow is idempotent (created=false)")
        time.sleep(self.pause)

        # 14 ------------------------------------------------------------------
        self.title("Follow queries: PostgreSQL is the only source of truth")
        self.show(
            "alice followers",
            self.call("GET", f"/api/v1/users/{self.ids['alice']}/followers"),
        )
        self.show(
            "bob following",
            self.call("GET", f"/api/v1/users/{self.ids['bob']}/following"),
        )
        self.note(
            "there is no separate graph store: Member 9's graph module is excluded "
            "from this iteration, so relationships are read from the follows table"
        )
        time.sleep(self.pause)

        # 15 ------------------------------------------------------------------
        self.title("Feed (Member 7): pull vs fan-out-on-write")
        strategies = self.call("GET", "/api/v1/feed/strategies")
        self.show("active strategy", strategies)
        feed = self.call("GET", f"/api/v1/users/{self.ids['bob']}/feed?limit=5")
        self.show("bob's feed", feed)
        if strategies.get("strategy") == "push":
            self.note(
                "push is empty here on purpose: bob followed alice AFTER the post "
                "was written, and fan-out-on-write only reaches followers that "
                "exist at write time - the trade-off the strategy documents"
            )
            self.show(
                "rebuild fan-out table from PostgreSQL",
                self.call("POST", "/api/v1/feed/rebuild"),
            )
            self.show(
                "bob's feed after rebuild",
                self.call("GET", f"/api/v1/users/{self.ids['bob']}/feed?limit=5"),
            )
        else:
            self.note(
                "FEED_STRATEGY=pull is active; restart with FEED_STRATEGY=push to "
                "see the derived user_feed table and its rebuild"
            )
        time.sleep(self.pause)

        # 16 ------------------------------------------------------------------
        self.title("Search (Member 10): a derived index over canonical ids")
        self.show("POST /api/v1/search/reindex", self.call("POST", "/api/v1/search/reindex"))
        hits = self.call("GET", "/api/v1/search?q=metascale&limit=5")
        self.show("q=metascale", hits)
        self.show(
            "autocomplete prefix=meta",
            self.call("GET", "/api/v1/search/autocomplete?prefix=meta"),
        )
        self.note("results carry canonical post_id / user_id, never index-local ids")
        time.sleep(self.pause)

        # 17 ------------------------------------------------------------------
        self.title("Media (Member 8): SHA-256 content addressing, real de-duplication")
        # unique bytes per run so the first upload really is an upload and the
        # second one is the de-duplication case
        blob = f"metascale-demo-blob-{stamp}-".encode() + b"x" * 512
        boundary = "----metascaleDemoBoundary"

        def upload(filename: str) -> bytes:
            parts = [
                f"--{boundary}\r\n",
                'Content-Disposition: form-data; name="owner_id"\r\n\r\n',
                f"{self.ids['alice']}\r\n",
                f"--{boundary}\r\n",
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n',
                "Content-Type: image/png\r\n\r\n",
            ]
            body = "".join(parts).encode() + blob + f"\r\n--{boundary}--\r\n".encode()
            return body

        first_upload = self.call(
            "POST",
            "/api/v1/media/upload",
            form=upload("demo.png"),
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        second_upload = self.call(
            "POST",
            "/api/v1/media/upload",
            form=upload("demo-copy.png"),
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        self.ids["media"] = first_upload["media_id"]
        self.show("first upload", first_upload)
        self.show("second upload", second_upload)
        self.show("same media_id", first_upload["media_id"] == second_upload["media_id"])
        self.show("backend", self.call("GET", "/api/v1/media/_stats/backend"))
        time.sleep(self.pause)

        # 18 ------------------------------------------------------------------
        self.title("Denormalized read model (Member 3): derived, event-fed, rebuildable")
        self.show("stats", self.call("GET", "/api/v1/read-model/stats"))
        self.show("rebuild", self.call("POST", "/api/v1/read-model/rebuild"))
        self.show("single-table read", self.call("GET", "/api/v1/read-model/posts?limit=3"))
        time.sleep(self.pause)

        # 19 ------------------------------------------------------------------
        self.title("Sharding internals: distribution, hot keys, routing table")
        self.show("shards", self.call("GET", "/api/v1/shards"))
        self.show("distribution", self.call("GET", "/api/v1/shards/distribution"))
        stats = self.call("GET", "/api/v1/shards/stats")
        self.show(
            "stats",
            (
                {k: stats[k] for k in ("shard_count", "total_requests", "healthy_shards")}
                if isinstance(stats, dict)
                else stats
            ),
        )
        self.show(
            "route a raw key",
            self.call("GET", f"/api/v1/shards/route/{self.ids['post']}"),
        )
        time.sleep(self.pause)

        # 20 ------------------------------------------------------------------
        self.title("Replication and observability: what is real, what is labelled")
        self.show("replication status", self.call("GET", "/api/v1/replication/status"))
        simulated = self.call(
            "POST", f"/api/v1/replication/demo/simulate-failover/{route['shard_id']}"
        )
        self.show("simulated failover", simulated)
        self.note(
            "the response carries simulated=true: no PostgreSQL command is issued. "
            "Real promotion is POST /api/v1/replication/promote/{shard_id}, which "
            "refuses when no standby is configured."
        )
        metrics = self.call("GET", "/api/v1/metrics")
        self.show(
            "metrics (abridged)",
            {
                "counters": {k: v for k, v in metrics.get("counters", {}).items() if v},
                "request_latency_ms": metrics.get("histograms", {}).get("request_latency_ms"),
                "cache": {
                    k: metrics.get("cache", {}).get(k)
                    for k in ("cache_hits", "cache_misses", "hit_ratio", "backend")
                },
                "events": metrics.get("events"),
            },
        )
        prometheus = _request("GET", f"{self.base}/metrics")[2]
        lines = [ln for ln in str(prometheus).splitlines() if ln.startswith("metascale_")]
        self.show("prometheus metric families", len({ln.split("{")[0] for ln in lines}))
        self.show("sample", lines[:4])

        print()
        print("=" * STEP_WIDTH)
        print(f"DEMO COMPLETE — {self.step} steps, all executed against {self.base}")
        print("=" * STEP_WIDTH)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the 20-step MetaScale demo")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--pause",
        type=float,
        default=0.6,
        help="seconds between steps (0 for a fast run)",
    )
    args = parser.parse_args()
    return Demo(args.base_url, args.pause).run()


if __name__ == "__main__":
    sys.exit(main())
