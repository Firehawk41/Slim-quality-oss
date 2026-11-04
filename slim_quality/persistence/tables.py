"""slim-quality's own tables. Append-only, like slim-data's: rows are inserted, never updated or deleted,
enforced by the repositories (no update path) and by triggers (`append_only.py`). A change is a
request row, then a decision row; an approval adds a profile version or a limit set.

Portable: enums as ints, JSON via `JSON`, no ARRAY, no foreign keys into other modules' tables.
People are stored as id + the whole actor as JSON, as they stood at the time.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

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


# --- phase 5a: equipment, reference lots, PT rounds, complaints (D-125…D-130) ------------------------
# Each is a header row and its entries (the whole entry as JSON, as for nonconformities).

quality_equipment = Table(
    "quality_equipment",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(100), nullable=False, unique=True),  # as slim-data and slim-prep record it
    Column("kind", Integer, nullable=False),  # EquipmentKind
    Column("description", Text, nullable=False),
    Column("calibration_required", Boolean, nullable=False),
    *_by(),
    _created(),
)

quality_equipment_entries = Table(
    "quality_equipment_entries",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("equipment_id", ForeignKey("quality_equipment.id"), nullable=False, index=True),
    Column("kind", Integer, nullable=False),  # EquipmentEntryKind
    Column("entry", JSON, nullable=False),
    *_by(),
    _created(),
)

quality_lots = Table(
    "quality_lots",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(100), nullable=False),
    Column("lot", String(50), nullable=False),
    Column("lot_record", JSON, nullable=False),  # the lot as registered (certificate and all)
    *_by(),
    _created(),
    UniqueConstraint("name", "lot", name="uq_quality_lots_name_lot"),
)

quality_lot_entries = Table(
    "quality_lot_entries",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("lot_id", ForeignKey("quality_lots.id"), nullable=False, index=True),
    Column("kind", Integer, nullable=False),  # LotEntryKind
    Column("entry", JSON, nullable=False),
    *_by(),
    _created(),
)

quality_pt_rounds = Table(
    "quality_pt_rounds",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("scheme", String(100), nullable=False),
    Column("round_code", String(50), nullable=False),
    Column("tr_number", String(20), nullable=False, index=True),
    Column("round_record", JSON, nullable=False),
    *_by(),
    _created(),
    UniqueConstraint("scheme", "round_code", name="uq_quality_pt_rounds_scheme_round"),
)

quality_pt_entries = Table(
    "quality_pt_entries",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("round_id", ForeignKey("quality_pt_rounds.id"), nullable=False, index=True),
    Column("kind", Integer, nullable=False),  # PtEntryKind
    Column("entry", JSON, nullable=False),
    *_by(),
    _created(),
)

quality_complaints = Table(
    "quality_complaints",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("tr_number", String(20), nullable=False, index=True),
    Column("customer_id", String(50), nullable=False, index=True),
    Column("complaint", JSON, nullable=False),  # as raised
    *_by(),
    _created(),
)

quality_complaint_entries = Table(
    "quality_complaint_entries",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("complaint_id", ForeignKey("quality_complaints.id"), nullable=False, index=True),
    Column("kind", Integer, nullable=False),  # ComplaintEntryKind
    Column("entry", JSON, nullable=False),
    *_by(),
    _created(),
)

TABLES_5A = (
    "quality_equipment",
    "quality_equipment_entries",
    "quality_lots",
    "quality_lot_entries",
    "quality_pt_rounds",
    "quality_pt_entries",
    "quality_complaints",
    "quality_complaint_entries",
)
TABLES = TABLES + TABLES_5A


# --- phase 5b: uncertainty estimates, the authorisation register (D-131, D-132) ----------------------

quality_uncertainty = Table(
    "quality_uncertainty",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("key", String(250), nullable=False, index=True),  # UncertaintyKey.slug
    Column("estimate", JSON, nullable=False),  # the whole estimate, inputs and all, as signed
    *_by(),
    _created(),
)

quality_authorisations = Table(
    "quality_authorisations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("person_id", String(100), nullable=False, index=True),
    Column("act", Integer, nullable=False),  # Act
    Column("authorisation", JSON, nullable=False),  # as granted
    *_by(),
    _created(),
)

quality_authorisation_entries = Table(
    "quality_authorisation_entries",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("authorisation_id", ForeignKey("quality_authorisations.id"), nullable=False, index=True),
    Column("kind", Integer, nullable=False),  # AuthorisationEntryKind
    Column("entry", JSON, nullable=False),
    *_by(),
    _created(),
)

TABLES_5B = ("quality_uncertainty", "quality_authorisations", "quality_authorisation_entries")
TABLES = TABLES + TABLES_5B
