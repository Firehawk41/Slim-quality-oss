"""Uncertainty estimates and the authorisation register with its entries (D-131, D-132); append-only.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_quality.persistence import append_only
from slim_quality.persistence.types import UtcDateTime, db_now

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("quality_uncertainty", "quality_authorisations", "quality_authorisation_entries")


def _by() -> list[sa.Column]:
    return [sa.Column("by_id", sa.String(100), nullable=False), sa.Column("by", sa.JSON(), nullable=False)]


def _created() -> sa.Column:
    return sa.Column("created_at", UtcDateTime(), nullable=False, server_default=db_now())


def upgrade() -> None:
    op.create_table(
        "quality_uncertainty",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(250), nullable=False),
        sa.Column("estimate", sa.JSON(), nullable=False),
        *_by(),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_uncertainty")),
    )
    op.create_index(op.f("ix_quality_uncertainty_key"), "quality_uncertainty", ["key"])
    op.create_table(
        "quality_authorisations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.String(100), nullable=False),
        sa.Column("act", sa.Integer(), nullable=False),
        sa.Column("authorisation", sa.JSON(), nullable=False),
        *_by(),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_authorisations")),
    )
    op.create_index(op.f("ix_quality_authorisations_person_id"), "quality_authorisations", ["person_id"])
    op.create_table(
        "quality_authorisation_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("authorisation_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Integer(), nullable=False),
        sa.Column("entry", sa.JSON(), nullable=False),
        *_by(),
        _created(),
        sa.ForeignKeyConstraint(
            ["authorisation_id"],
            ["quality_authorisations.id"],
            name=op.f("fk_quality_authorisation_entries_authorisation_id_quality_authorisations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_authorisation_entries")),
    )
    op.create_index(
        op.f("ix_quality_authorisation_entries_authorisation_id"),
        "quality_authorisation_entries",
        ["authorisation_id"],
    )
    append_only.install(op.get_bind(), TABLES)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
