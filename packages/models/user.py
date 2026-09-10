"""users — account credentials/identity.

3NF: display/profile attributes live in ``profiles``. Intentionally has *no*
SQLAlchemy relationships: in SHARDED mode related rows can live on different
physical shards, where a local SQL JOIN / lazy load is impossible. Cross-shard
assembly is done explicitly in the query layer.
"""

from __future__ import annotations

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin, pk_column


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        # username/email are unique login identifiers -> unique indexes.
        Index("ix_users_username", "username", unique=True),
        Index("ix_users_email", "email", unique=True),
    )

    id: Mapped[int] = pk_column()
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<User id={self.id} username={self.username!r}>"
