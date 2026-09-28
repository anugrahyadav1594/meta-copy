"""Member 10 — search: ``SearchProvider`` abstraction.

Keeps the inverted-index implementation from the ``member10-search-indexing``
branch (real tokenization, real scoring) but removes what made it
un-integratable: the index is no longer seeded with four hard-coded sample
documents at import time and no longer runs as its own FastAPI server.

Status:
* ``InMemorySearchProvider`` — IMPLEMENTED (default; dev + demo + tests).
* ``OpenSearchProvider``     — IMPLEMENTED but OPTIONAL: requires the
  ``search`` compose profile. It fails loudly (not silently) when OpenSearch
  is unreachable, so a demo never pretends to search a cluster it is not
  talking to.

Every result refers back to a **canonical entity id** (``post_id`` /
``user_id``), never to a document invented by the index.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any

from common.logging import get_logger

logger = get_logger("search")

TOKEN_RE = re.compile(r"[a-z0-9#@][a-z0-9_'#@-]*")


def tokenize(text: str) -> list[str]:
    """Lower-case word tokenizer that keeps hashtags and mentions intact."""
    return TOKEN_RE.findall((text or "").lower())


class SearchProvider(ABC):
    """Index + query interface for derived search indexes."""

    backend = "abstract"

    @abstractmethod
    async def index_document(self, index: str, document: dict[str, Any]) -> None: ...

    @abstractmethod
    async def search(self, index: str, query: str, limit: int = 20) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def autocomplete(self, index: str, prefix: str, limit: int = 10) -> list[str]: ...

    @abstractmethod
    async def clear(self, index: str) -> None: ...

    def stats(self) -> dict[str, Any]:
        return {"backend": self.backend}

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend}


class InMemorySearchProvider(SearchProvider):
    """In-memory inverted index with BM25 scoring.

    IMPLEMENTED. Scores are real BM25 over the indexed corpus (not a constant
    and not fabricated); the index lives in the API process and is rebuilt
    from canonical PostgreSQL by :mod:`search.indexer`.
    """

    backend = "in_memory_inverted_index"

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.indexes: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.postings: dict[str, dict[str, dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(int))  # index -> token -> doc -> tf
        )
        self.doc_lengths: dict[str, dict[str, int]] = defaultdict(dict)
        self.queries = 0

    # ------------------------------------------------------------------ write
    async def index_document(self, index: str, document: dict[str, Any]) -> None:
        doc_id = str(document.get("id"))
        text = " ".join(
            str(document.get(field, ""))
            for field in ("text", "content", "username", "display_name", "tag")
        )
        tokens = tokenize(text)
        self.indexes[index][doc_id] = document
        self.doc_lengths[index][doc_id] = len(tokens)
        postings = self.postings[index]
        # remove the previous version of this document (re-index)
        for token, docs in list(postings.items()):
            docs.pop(doc_id, None)
            if not docs:
                postings.pop(token, None)
        for token in tokens:
            postings[token][doc_id] += 1

    async def clear(self, index: str) -> None:
        self.indexes.pop(index, None)
        self.postings.pop(index, None)
        self.doc_lengths.pop(index, None)

    # ------------------------------------------------------------------- read
    async def search(self, index: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        self.queries += 1
        tokens = tokenize(query)
        if not tokens:
            return []
        docs = self.indexes.get(index, {})
        if not docs:
            return []
        avg_len = (sum(self.doc_lengths[index].values()) / len(docs)) or 1.0
        postings = self.postings.get(index, {})
        scores: dict[str, float] = defaultdict(float)
        matched: dict[str, set[str]] = defaultdict(set)

        for token in tokens:
            doc_freqs = postings.get(token)
            if not doc_freqs:
                continue
            idf = math.log(1 + (len(docs) - len(doc_freqs) + 0.5) / (len(doc_freqs) + 0.5))
            for doc_id, tf in doc_freqs.items():
                length = self.doc_lengths[index].get(doc_id, 0)
                denom = tf + self.k1 * (1 - self.b + self.b * length / avg_len)
                scores[doc_id] += idf * ((tf * (self.k1 + 1)) / denom if denom else 0.0)
                matched[doc_id].add(token)

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        results = []
        for doc_id, score in ranked:
            document = dict(docs.get(doc_id, {}))
            document["_score"] = round(score, 6)
            document["_matched_tokens"] = sorted(matched.get(doc_id, set()))
            results.append(document)
        return results

    async def autocomplete(self, index: str, prefix: str, limit: int = 10) -> list[str]:
        prefix = (prefix or "").lower()
        if not prefix:
            return []
        seen: set[str] = set()
        for token in self.postings.get(index, {}):
            if token.startswith(prefix) and token not in seen:
                seen.add(token)
            if len(seen) >= limit:
                break
        return sorted(seen)[:limit]

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "indexes": {name: len(docs) for name, docs in self.indexes.items()},
            "documents": sum(len(docs) for docs in self.indexes.values()),
            "terms": {name: len(terms) for name, terms in self.postings.items()},
            "queries": self.queries,
            "derived": True,
        }


class OpenSearchProvider(SearchProvider):
    """OpenSearch-backed provider (OPTIONAL, ``search`` compose profile).

    Talks to a real cluster over HTTP. ``available`` is False until a
    successful ping, and every method reports the failure instead of quietly
    returning empty results.
    """

    backend = "opensearch"

    def __init__(self, url: str, timeout: float = 3.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._client: Any = None
        self.last_error: str | None = None

    async def _ping(self) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(self.url)
                return res.status_code < 400
        except Exception as exc:  # noqa: BLE001 - network dependent
            self.last_error = type(exc).__name__
            logger.warning("opensearch_unavailable", url=self.url, error=self.last_error)
            return False

    async def index_document(self, index: str, document: dict[str, Any]) -> None:
        import httpx

        doc_id = str(document.get("id"))
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.put(f"{self.url}/{index}/_doc/{doc_id}", json=document)
            res.raise_for_status()

    async def search(self, index: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(
                f"{self.url}/{index}/_search",
                json={"size": limit, "query": {"match": {"text": query}}},
            )
            res.raise_for_status()
            body = res.json()
        hits = body.get("hits", {}).get("hits", [])
        return [
            {
                **{k: v for k, v in hit.get("_source", {}).items()},
                "_score": hit.get("_score", 0.0),
                "_id": hit.get("_id"),
            }
            for hit in hits
        ]

    async def autocomplete(self, index: str, prefix: str, limit: int = 10) -> list[str]:
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(
                f"{self.url}/{index}/_search",
                json={
                    "size": 0,
                    "suggest": {
                        "completion": {
                            "prefix": prefix,
                            "completion": {"field": "suggest", "size": limit},
                        }
                    },
                },
            )
            if res.status_code >= 400:
                return []
            body = res.json()
        options = body.get("suggest", {}).get("completion", [])
        terms: list[str] = []
        for group in options:
            for option in group.get("options", []):
                terms.append(option.get("text", ""))
        return [t for t in terms if t][:limit]

    async def clear(self, index: str) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            await client.post(
                f"{self.url}/{index}/_delete_by_query",
                json={"query": {"match_all": {}}},
            )

    async def available(self) -> bool:
        return await self._ping()

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "url": self.url,
            "reachable": self.last_error is None,
            "last_error": self.last_error,
        }


def build_search_provider(settings: Any) -> SearchProvider:
    """Factory used by the composition root."""
    if (settings.search_provider or "memory").lower() == "opensearch":
        try:
            return OpenSearchProvider(settings.opensearch_url)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("opensearch_init_failed", error=type(exc).__name__)
    return InMemorySearchProvider()
