"""notifications — shard key in SHARDED mode: ``recipient_id``.

Co-locates every notification in the recipient's inbox shard. ``actor_id`` and
the polymorphic ``reference_id`` have no FK: they may point at rows on other
shards.
"""

from __future__ import annotations

from common.enums import NotificationType
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, CreatedMixin, pk_column


class Notification(Base, CreatedMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        # Inbox query: recipient's notifications, newest first.
        Index("ix_notifications_recipient_created", "recipient_id", "created_at"),
        Index("ix_notifications_recipient_unread", "recipient_id", "is_read"),
        CheckConstraint(
            f"type IN ('{NotificationType.LIKE.value}', "
            f"'{NotificationType.COMMENT.value}', "
            f"'{NotificationType.FOLLOW.value}', "
            f"'{NotificationType.MENTION.value}', "
            f"'{NotificationType.SYSTEM.value}')",
            name="ck_notifications_type",
        ),
    )

    id: Mapped[int] = pk_column()
    # FK valid in BOTH modes: notifications are sharded by recipient_id.
    recipient_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # No FK: the actor can live on another shard.
    actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    reference_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
