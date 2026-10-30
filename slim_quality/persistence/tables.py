"""slim-quality's own tables. Append-only, like slim-data's: rows are inserted, never updated or deleted,
enforced by the repositories (no update path) and by triggers (`append_only.py`). A change is a
request row, then a decision row; an approval adds a profile version or a limit set.

Portable: enums as ints, JSON via `JSON`, no ARRAY, no foreign keys into other modules' tables.
People are stored as id + the whole actor as JSON, as they stood at the time.
"""

from sqlalchemy import JSON, Boolean, Column, Date, ForeignKey, Index, Integer, String, Table, Text

from slim_quality.persistence.db import Base
from slim_quality.persistence.types import UtcDateTime, db_now

metadata = Base.metadata


def _by(prefix: str = "by") -> list[Column]:
    return [Column(f"{prefix}_id", String(100), nullable=False), Column(prefix, JSON, nullable=False)]


def _created() -> Column:
    return Column("created_at", UtcDateTime(), nullable=False, server_default=db_now())


quality_changes = Table(
    "quality_changes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("kind", Integer, nullable=False),  # ChangeKind
    Column("payload", JSON, nullable=False),  # the proposed profile or limit set
    Column("reason", Text, nullable=False),
    *_by("proposed_by"),
    _created(),
)

quality_decisions = Table(
    "quality_decisions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("change_id", ForeignKey("quality_changes.id"), nullable=False, unique=True),  # decided once
    Column("approved", Boolean, nullable=False),
    Column("note", Text, nullable=False),
    *_by(),
    _created(),
)

quality_profiles = Table(
    "quality_profiles",
    metadata,
    Column("version", Integer, primary_key=True, autoincrement=False),  # 1, 2, ... (0 = starter)
    Column("change_id", ForeignKey("quality_changes.id"), nullable=False, unique=True),
    Column("profile", JSON, nullable=False),
    _created(),
)

quality_limit_sets = Table(
    "quality_limit_sets",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("change_id", ForeignKey("quality_changes.id"), nullable=False, unique=True),
    Column("series", String(400), nullable=False),  # SeriesKey.slug
    Column("limit_set", JSON, nullable=False),
    Column("effective_from", UtcDateTime(), nullable=False),
    _created(),
    Index("ix_quality_limit_sets_series", "series"),
)

quality_events = Table(
    "quality_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("kind", Integer, nullable=False),  # EventKind
    Column("instrument", String(100), nullable=False),
    Column("analyte", String(50)),
    Column("at", UtcDateTime(), nullable=False),
    Column("note", Text, nullable=False),
    *_by(),
    _created(),
)

quality_notes = Table(
    "quality_notes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("series", String(400), nullable=False),
    Column("ref", String(100), nullable=False),
    Column("note", Text, nullable=False),
    *_by(),
    _created(),
    Index("ix_quality_notes_series", "series"),
)

quality_reviews = Table(
    "quality_reviews",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("period_from", Date, nullable=False),
    Column("period_to", Date, nullable=False),
    Column("summary", JSON, nullable=False),
    Column("comment", Text, nullable=False),
    *_by(),
    _created(),
)

quality_audits = Table(
    "quality_audits",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("subject", String(200), nullable=False),
    Column("subject_ref", String(100), nullable=False, index=True),
    Column("outcome", Integer, nullable=False),  # AuditOutcome
    Column("findings", Text, nullable=False),
    *_by(),
    _created(),
)

quality_ncs = Table(
    "quality_ncs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("title", String(200), nullable=False),
    Column("description", Text, nullable=False),
    Column("source", Integer, nullable=False),  # NcSource
    Column("source_ref", String(200), nullable=False),
    Column("instrument", String(100)),
    Column("analyte", String(50)),
    Column("window_from", UtcDateTime()),
    Column("window_to", UtcDateTime()),
    *_by(),
    _created(),
)

quality_nc_entries = Table(
    "quality_nc_entries",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("nc_id", ForeignKey("quality_ncs.id"), nullable=False, index=True),
    Column("kind", Integer, nullable=False),  # EntryKind
    Column("entry", JSON, nullable=False),  # the whole entry, as recorded
    *_by(),
    _created(),
)

TABLES = (
    "quality_changes",
    "quality_decisions",
    "quality_profiles",
    "quality_limit_sets",
    "quality_events",
    "quality_notes",
    "quality_reviews",
    "quality_audits",
    "quality_ncs",
    "quality_nc_entries",
)
