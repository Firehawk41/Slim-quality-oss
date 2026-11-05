"""Risks, internal audits, management reviews and validation signatures (D-134…D-137); append-only.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_quality.persistence import append_only
from slim_quality.persistence.types import UtcDateTime, db_now

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "quality_risks",
    "quality_risk_entries",
    "quality_internal_audits",
    "quality_internal_audit_entries",
    "quality_management_reviews",
    "quality_validation_signatures",
)


def _by() -> list[sa.Column]:
    return [sa.Column("by_id", sa.String(100), nullable=False), sa.Column("by", sa.JSON(), nullable=False)]


def _created() -> sa.Column:
    return sa.Column("created_at", UtcDateTime(), nullable=False, server_default=db_now())


def _header(name: str, *columns: sa.Column, index: str | None = None) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.Integer(), nullable=False),
        *columns,
        *_by(),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{name}")),
    )
    if index is not None:
        op.create_index(op.f(f"ix_{name}_{index}"), name, [index])


def _entries(name: str, parent: str, parent_table: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(parent, sa.Integer(), nullable=False),
        sa.Column("kind", sa.Integer(), nullable=False),
        sa.Column("entry", sa.JSON(), nullable=False),
        *_by(),
        _created(),
        sa.ForeignKeyConstraint(
            [parent], [f"{parent_table}.id"], name=op.f(f"fk_{name}_{parent}_{parent_table}")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{name}")),
    )
    op.create_index(op.f(f"ix_{name}_{parent}"), name, [parent])


def upgrade() -> None:
    _header(
        "quality_risks",
        sa.Column("kind", sa.Integer(), nullable=False),
        sa.Column("risk", sa.JSON(), nullable=False),
    )
    _entries("quality_risk_entries", "risk_id", "quality_risks")
    _header(
        "quality_internal_audits",
        sa.Column("clause", sa.String(20), nullable=False),
        sa.Column("planned_for", sa.Date(), nullable=False),
        sa.Column("audit", sa.JSON(), nullable=False),
        index="clause",
    )
    _entries("quality_internal_audit_entries", "audit_id", "quality_internal_audits")
    _header(
        "quality_management_reviews",
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("review", sa.JSON(), nullable=False),
    )
    _header(
        "quality_validation_signatures",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("signature", sa.JSON(), nullable=False),
        index="key",
    )
    append_only.install(op.get_bind(), TABLES)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
