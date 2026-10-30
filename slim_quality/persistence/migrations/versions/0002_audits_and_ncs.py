"""Audit records (D-113) and nonconformities with their entries (D-114); append-only.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_quality.persistence import append_only
from slim_quality.persistence.types import UtcDateTime, db_now

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("quality_audits", "quality_ncs", "quality_nc_entries")


def _by() -> list[sa.Column]:
    return [sa.Column("by_id", sa.String(100), nullable=False), sa.Column("by", sa.JSON(), nullable=False)]


def _created() -> sa.Column:
    return sa.Column("created_at", UtcDateTime(), nullable=False, server_default=db_now())


def upgrade() -> None:
    op.create_table(
        "quality_audits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("subject_ref", sa.String(100), nullable=False),
        sa.Column("outcome", sa.Integer(), nullable=False),
        sa.Column("findings", sa.Text(), nullable=False),
        *_by(),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_audits")),
    )
    op.create_index(op.f("ix_quality_audits_subject_ref"), "quality_audits", ["subject_ref"])
    op.create_table(
        "quality_ncs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(200), nullable=False),
        sa.Column("instrument", sa.String(100), nullable=True),
        sa.Column("analyte", sa.String(50), nullable=True),
        sa.Column("window_from", UtcDateTime(), nullable=True),
        sa.Column("window_to", UtcDateTime(), nullable=True),
        *_by(),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_ncs")),
    )
    op.create_table(
        "quality_nc_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nc_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Integer(), nullable=False),
        sa.Column("entry", sa.JSON(), nullable=False),
        *_by(),
        _created(),
        sa.ForeignKeyConstraint(
            ["nc_id"], ["quality_ncs.id"], name=op.f("fk_quality_nc_entries_nc_id_quality_ncs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_nc_entries")),
    )
    op.create_index(op.f("ix_quality_nc_entries_nc_id"), "quality_nc_entries", ["nc_id"])
    append_only.install(op.get_bind(), TABLES)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
