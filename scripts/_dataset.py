"""Deterministic social-media dataset generation (no third-party faker).

A single fixed RNG seed makes every run reproduce the exact same rows, so all
team members and all environments share the same mock dataset. When a
``ShardRouter`` is supplied, derived ids (posts, media) are *key-aligned* so
rows land on — and stay findable on — the correct shard.
"""

from __future__ import annotations

import random
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any

SIZES: dict[str, dict[str, int]] = {
    "small": {
        "users": 100,
        "posts": 1_000,
        "comments": 5_000,
        "likes": 10_000,
        "follows": 2_000,
        "media": 200,
        "notifications": 2_000,
    },
    "medium": {
        "users": 1_000,
        "posts": 25_000,
        "comments": 100_000,
        "likes": 250_000,
        "follows": 25_000,
        "media": 2_000,
        "notifications": 50_000,
    },
    "large": {
        # Hardware-tunable; override via SEED_* environment variables.
        "users": 5_000,
        "posts": 200_000,
        "comments": 800_000,
        "likes": 2_000_000,
        "follows": 150_000,
        "media": 10_000,
        "notifications": 300_000,
    },
}

FIRST = [
    "ada",
    "alan",
    "grace",
    "linus",
    "margaret",
    "dennis",
    "katherine",
    "norman",
    "radia",
    "barbara",
    "edsger",
    "donald",
    "ken",
    "frances",
    "ivan",
    "vint",
    "tim",
    "sally",
    "guido",
    "brendan",
]
SECOND = [
    "lovelace",
    "turing",
    "hopper",
    "torvalds",
    "hamilton",
    "ritchie",
    "johnson",
    "perlman",
    "liskov",
    "dijkstra",
    "knuth",
    "thompson",
    "allen",
    "sutherland",
    "cerf",
    "berners",
    "floyd",
    "rossum",
    "eich",
    "abramson",
]
WORDS = (
    "distributed shard consensus replication cache hash ring virtual node latency "
    "throughput canonical normalized index foreign key query transaction replica "
    "feed graph edge blob storage search observability throughput partition migration "
    "checksum router scatter gather hotspot balance consistency availability"
).split()
MEDIA = [
    ("image", "image/jpeg", "jpg"),
    ("video", "video/mp4", "mp4"),
    ("audio", "audio/mpeg", "mp3"),
    ("document", "application/pdf", "pdf"),
]
NOTIF_TYPES = ["like", "comment", "follow", "mention", "system"]
VISIBILITY = ["public", "public", "public", "followers", "private"]

BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _ts(offset: int) -> datetime:
    return BASE_TIME + timedelta(seconds=offset * 7)


class DatasetBuilder:
    def __init__(
        self, size: str = "small", seed: int = 20260910, overrides: dict[str, int] | None = None
    ):
        self.size_name = size
        counts = dict(SIZES[size])
        if overrides:
            counts.update({k: v for k, v in overrides.items() if v is not None})
        self.c = counts
        self.rng = random.Random(seed)
        self.seed = seed
        # Monotonic cursors for key-aligned id generation (shard mode).
        self._media_cursor = 10_000_000
        self._post_cursor = 20_000_000

    def _aligned_id(self, cursor_attr: str, shard: str, router: Any) -> int:
        """Next strictly-increasing id routing to ``shard``."""
        cursor = getattr(self, cursor_attr) + 1
        value = router.generate_key_routed_to(shard, base=cursor)
        setattr(self, cursor_attr, value)
        return value

    def sentence(self) -> str:
        n = self.rng.randint(6, 14)
        words = [self.rng.choice(WORDS) for _ in range(n)]
        words[0] = words[0].capitalize()
        return " ".join(words) + "."

    def build(self, router: Any | None = None) -> OrderedDict[str, list[dict[str, Any]]]:
        c = self.c
        rng = self.rng
        U = c["users"]

        # ----- users / profiles ------------------------------------------
        users, profiles = [], []
        used_names: set[str] = set()
        for uid in range(1, U + 1):
            base = f"{rng.choice(FIRST)}_{rng.choice(SECOND)}"
            name = base
            suffix = 1
            while name in used_names:
                suffix += 1
                name = f"{base}{suffix}"
            used_names.add(name)
            users.append(
                {
                    "id": uid,
                    "username": name if uid > 20 else f"{name}",
                    "email": f"{name}@example.test",
                    "password_hash": "seed-no-login",
                    "created_at": _ts(uid),
                    "updated_at": _ts(uid),
                }
            )
            profiles.append(
                {
                    "id": uid,  # 1:1 with user
                    "user_id": uid,
                    "display_name": name.replace("_", " ").title(),
                    "bio": self.sentence() if rng.random() < 0.6 else None,
                    "avatar_media_id": None,
                    "created_at": _ts(uid),
                    "updated_at": _ts(uid),
                }
            )

        # ----- media (aligned to owner shard when sharding) --------------
        media = []
        media_seq = 0
        for i in range(c["media"]):
            owner = rng.randint(1, U)
            if router is not None:
                shard = router.shard_for(owner)
                mid = self._aligned_id("_media_cursor", shard, router)
            else:
                media_seq += 1
                mid = media_seq
            mtype, mime, ext = rng.choice(MEDIA)
            media.append(
                {
                    "id": mid,
                    "owner_id": owner,
                    "storage_key": f"haystack-inspired/{mtype}/{mid}.{ext}",
                    "media_type": mtype,
                    "mime_type": mime,
                    "size_bytes": rng.randint(1_000, 5_000_000),
                    "created_at": _ts(i),
                }
            )
        # wire a deterministic subset of avatars
        for p, m in zip(profiles, media, strict=False):
            if m["owner_id"] == p["user_id"]:
                p["avatar_media_id"] = m["id"]

        # ----- posts (aligned to author shard when sharding) -------------
        posts, post_meta = [], []
        canonical_seq = 0
        # Zipf-ish skew: a few authors are much more prolific (seed hot users)
        weights = [1.0 / (i + 1) ** 1.1 for i in range(U)]
        for i in range(c["posts"]):
            author = rng.choices(range(1, U + 1), weights=weights, k=1)[0]
            if router is not None:
                shard = router.shard_for(author)
                pid = self._aligned_id("_post_cursor", shard, router)
            else:
                canonical_seq += 1
                pid = canonical_seq
            posts.append(
                {
                    "id": pid,
                    "author_id": author,
                    "content": self.sentence(),
                    "visibility": rng.choice(VISIBILITY),
                    "created_at": _ts(10_000 + i),
                    "updated_at": _ts(10_000 + i),
                }
            )
            post_meta.append((pid, author))
        post_ids = [p[0] for p in post_meta]

        # ----- follows ----------------------------------------------------
        follows, seen_edges = [], set()
        target = min(c["follows"], U * (U - 1) // 2)
        while len(follows) < target:
            a, b = rng.randint(1, U), rng.randint(1, U)
            if a == b or (a, b) in seen_edges:
                continue
            seen_edges.add((a, b))
            follows.append(
                {
                    "follower_id": a,
                    "following_id": b,
                    "created_at": _ts(30_000 + len(follows)),
                }
            )

        # ----- comments (shard key: post_id; co-located with the post) ----
        comments = []
        for i in range(c["comments"]):
            pid, author = post_meta[rng.randrange(len(post_meta))]
            commenter = rng.randint(1, U)
            comments.append(
                {
                    "id": 40_000_000 + i,
                    "post_id": pid,
                    "user_id": commenter,
                    "content": self.sentence(),
                    "created_at": _ts(50_000 + i),
                }
            )

        # ----- likes (unique post/user) -----------------------------------
        likes, seen_likes = [], set()
        target_likes = min(c["likes"], len(post_ids) * U)
        while len(likes) < target_likes:
            pid = post_ids[rng.randrange(len(post_ids))]
            uid = rng.randint(1, U)
            if (pid, uid) in seen_likes:
                continue
            seen_likes.add((pid, uid))
            likes.append(
                {
                    "id": 80_000_000 + len(likes),
                    "post_id": pid,
                    "user_id": uid,
                    "created_at": _ts(70_000 + len(likes)),
                }
            )

        # ----- notifications ----------------------------------------------
        notifications = []
        for i in range(c["notifications"]):
            recipient = rng.randint(1, U)
            actor = rng.randint(1, U)
            ntype = rng.choice(NOTIF_TYPES)
            notifications.append(
                {
                    "id": 120_000_000 + i,
                    "recipient_id": recipient,
                    "actor_id": actor if actor != recipient else None,
                    "type": ntype,
                    "reference_id": (
                        rng.choice(post_ids) if ntype in ("like", "comment", "mention") else None
                    ),
                    "is_read": rng.random() < 0.4,
                    "created_at": _ts(90_000 + i),
                }
            )

        return OrderedDict(
            users=users,
            media=media,
            profiles=profiles,
            posts=posts,
            follows=follows,
            comments=comments,
            likes=likes,
            notifications=notifications,
        )

    def summary(self, rows: dict[str, list[dict[str, Any]]]) -> str:
        return ", ".join(f"{k}={len(v):,}" for k, v in rows.items())
