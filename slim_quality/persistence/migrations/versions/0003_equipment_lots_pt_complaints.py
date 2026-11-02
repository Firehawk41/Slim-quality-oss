"""The equipment register, reference lots, PT rounds and complaints, each with its entries (D-125…D-130);
append-only.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_quality.persistence import append_only
from slim_quality.persistence.types import UtcDateTime, db_now

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "quality_equipment",
    "quality_equipment_entries",
    "quality_lots",
    "quality_lot_entries",
    "quality_pt_rounds",
    "quality_pt_entries",
    "quality_complaints",
    "quality_complaint_entries",
)


def _by() -> list[sa.Column]:
    return [sa.Column("by_id", sa.String(100), nullable=False), sa.Column("by", sa.JSON(), nullable=False)]


def _created() -> sa.Column:
    return sa.Column("created_at", UtcDateTime(), nullable=False, server_default=db_now())


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
    op.create_table(
        "quality_equipment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("calibration_required", sa.Boolean(), nullable=False),
        *_by(),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_equipment")),
        sa.UniqueConstraint("name", name=op.f("uq_quality_equipment_name")),
    )
    _entries("quality_equipment_entries", "equipment_id", "quality_equipment")
    op.create_table(
        "quality_lots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("lot", sa.String(50), nullable=False),
        sa.Column("lot_record", sa.JSON(), nullable=False),
        *_by(),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_lots")),
        sa.UniqueConstraint("name", "lot", name="uq_quality_lots_name_lot"),
    )
    _entries("quality_lot_entries", "lot_id", "quality_lots")
    op.create_table(
        "quality_pt_rounds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scheme", sa.String(100), nullable=False),
        sa.Column("round_code", sa.String(50), nullable=False),
        sa.Column("tr_number", sa.String(20), nullable=False),
        sa.Column("round_record", sa.JSON(), nullable=False),
        *_by(),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_pt_rounds")),
        sa.UniqueConstraint("scheme", "round_code", name="uq_quality_pt_rounds_scheme_round"),
    )
    op.create_index(op.f("ix_quality_pt_rounds_tr_number"), "quality_pt_rounds", ["tr_number"])
    _entries("quality_pt_entries", "round_id", "quality_pt_rounds")
    op.create_table(
        "quality_complaints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tr_number", sa.String(20), nullable=False),
        sa.Column("customer_id", sa.String(50), nullable=False),
        sa.Column("complaint", sa.JSON(), nullable=False),
        *_by(),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quality_complaints")),
    )
    op.create_index(op.f("ix_quality_complaints_tr_number"), "quality_complaints", ["tr_number"])
    op.create_index(op.f("ix_quality_complaints_customer_id"), "quality_complaints", ["customer_id"])
    _entries("quality_complaint_entries", "complaint_id", "quality_complaints")
    append_only.install(op.get_bind(), TABLES)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
