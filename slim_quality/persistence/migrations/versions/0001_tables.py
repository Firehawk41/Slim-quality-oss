"""slim-quality's tables (D-109): change requests and decisions, profile versions, limit sets, events,
notes and QC reviews; all append-only.

Revision ID: 0001
Revises:
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_quality.persistence import append_only
from slim_quality.persistence.types import UtcDateTime, db_now

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "quality_changes",
    "quality_decisions",
    "quality_profiles",
    "quality_limit_sets",
    "quality_events",
    "quality_notes",
    "quality_reviews",
)


def _by(prefix: str = "by") -> list[sa.Column]:
    return [
        sa.Column(f"{prefix}_id", sa.String(100), nullable=False),
        sa.Column(prefix, sa.JSON(), nullable=False),
    ]


def _created() -> sa.Column:
    return sa.Column("created_at", UtcDateTime(), nullable=False, server_default=db_now())


def upgrade() -> None:
    op.create_table(
        "quality_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        *_by("proposed_by"),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_changes")),
    )
    op.create_table(
        "quality_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("change_id", sa.Integer(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        *_by(),
        _created(),
        sa.ForeignKeyConstraint(
            ["change_id"], ["quality_changes.id"], name=op.f("fk_quality_decisions_change_id_quality_changes")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_decisions")),
        sa.UniqueConstraint("change_id", name=op.f("uq_quality_decisions_change_id")),
    )
    op.create_table(
        "quality_profiles",
        sa.Column("version", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("change_id", sa.Integer(), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["change_id"], ["quality_changes.id"], name=op.f("fk_quality_profiles_change_id_quality_changes")
        ),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_quality_profiles")),
        sa.UniqueConstraint("change_id", name=op.f("uq_quality_profiles_change_id")),
    )
    op.create_table(
        "quality_limit_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("change_id", sa.Integer(), nullable=False),
        sa.Column("series", sa.String(400), nullable=False),
        sa.Column("limit_set", sa.JSON(), nullable=False),
        sa.Column("effective_from", UtcDateTime(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["change_id"],
            ["quality_changes.id"],
            name=op.f("fk_quality_limit_sets_change_id_quality_changes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_limit_sets")),
        sa.UniqueConstraint("change_id", name=op.f("uq_quality_limit_sets_change_id")),
    )
    op.create_index("ix_quality_limit_sets_series", "quality_limit_sets", ["series"])
    op.create_table(
        "quality_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Integer(), nullable=False),
        sa.Column("instrument", sa.String(100), nullable=False),
        sa.Column("analyte", sa.String(50), nullable=True),
        sa.Column("at", UtcDateTime(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        *_by(),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_events")),
    )
    op.create_table(
        "quality_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("series", sa.String(400), nullable=False),
        sa.Column("ref", sa.String(100), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        *_by(),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_notes")),
    )
    op.create_index("ix_quality_notes_series", "quality_notes", ["series"])
    op.create_table(
        "quality_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        *_by(),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_reviews")),
    )
    append_only.install(op.get_bind(), TABLES)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    append_only.uninstall(op.get_bind())
