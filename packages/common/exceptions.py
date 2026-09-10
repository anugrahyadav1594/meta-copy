"""Structured exceptions for MetaScale.

All exceptions carry a stable machine-readable ``error_code`` and an optional
``details`` dict so the API layer can render consistent JSON error envelopes
*without* ever leaking stack traces, SQL, or credentials.
"""

from __future__ import annotations

from typing import Any


class MetaScaleError(Exception):
    """Base class for all structured application errors."""

    error_code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.error_code, "message": self.message}
        # only include non-sensitive, explicitly-provided details
        body.update(self.details)
        return body


class NotFoundError(MetaScaleError):
    error_code = "NOT_FOUND"
    http_status = 404


class ValidationError(MetaScaleError):
    error_code = "VALIDATION_ERROR"
    http_status = 422


class ConflictError(MetaScaleError):
    error_code = "CONFLICT"
    http_status = 409


# ---------------------------------------------------------------------------
# Sharding-specific errors (Member 4)
# ---------------------------------------------------------------------------


class ShardError(MetaScaleError):
    """Base class for shard-router errors."""


class ShardNotFoundError(ShardError):
    error_code = "SHARD_NOT_FOUND"
    http_status = 404

    def __init__(self, shard_id: str) -> None:
        super().__init__(
            f"Shard '{shard_id}' is not registered",
            {"shard_id": shard_id},
        )


class ShardUnavailableError(ShardError):
    error_code = "SHARD_UNAVAILABLE"
    http_status = 503

    def __init__(self, shard_id: str, reason: str = "currently unavailable") -> None:
        super().__init__(
            f"Target shard '{shard_id}' is {reason}",
            {"shard_id": shard_id, "reason": reason},
        )


class RoutingError(ShardError):
    error_code = "ROUTING_ERROR"
    http_status = 500

    def __init__(self, message: str, key: Any | None = None, strategy: str | None = None) -> None:
        details: dict[str, Any] = {}
        if key is not None:
            details["key"] = str(key)
        if strategy is not None:
            details["strategy"] = strategy
        super().__init__(message, details)


class CrossShardQueryError(ShardError):
    error_code = "CROSS_SHARD_QUERY_ERROR"
    http_status = 500


class RebalanceError(ShardError):
    error_code = "REBALANCE_ERROR"
    http_status = 409


class ChecksumMismatchError(ShardError):
    """Raised when source/destination verification fails during rebalancing.

    Ownership is NOT switched when this is raised — this guarantee is the
    core safety property of the migration workflow.
    """

    error_code = "CHECKSUM_MISMATCH"
    http_status = 409

    def __init__(self, source: dict[str, Any], destination: dict[str, Any]) -> None:
        super().__init__(
            "Checksum verification failed: refusing to switch shard ownership",
            {"source": source, "destination": destination},
        )


class NotConfiguredError(MetaScaleError):
    """Raised when a future module's extension point is invoked but not built."""

    error_code = "NOT_CONFIGURED"
    http_status = 501
