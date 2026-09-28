"""initial canonical social-media schema

Branches from an empty database and creates all canonical tables from the
shared SQLAlchemy metadata (single source of truth — see packages/models).
The same logical DDL exists as plain SQL for Docker first-boot:
infrastructure/postgres/init/002_schema.sql + 003_indexes.sql.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from models import Base

# Ensure every model is imported before metadata is used.
import models  # noqa: F401

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
