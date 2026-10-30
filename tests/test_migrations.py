"""slim-quality's migrations: reversible, matching the models, append-only, and kept to its own tables."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from slim_quality.persistence import migrate, tables  # noqa: F401  (registers the tables on Base)
from slim_quality.persistence.db import Base


def _version(connection) -> str | None:
    return MigrationContext.configure(
        connection, opts={"version_table": migrate.VERSION_TABLE}
    ).get_current_revision()


def test_upgrade_downgrade_upgrade(engine):
    with engine.begin() as connection:
        migrate.upgrade(connection)
        assert _version(connection) == "0001"
        migrate.downgrade(connection)
        assert _version(connection) is None
        migrate.upgrade(connection)
        migrate.downgrade(connection)


def test_models_match_migrations(engine):
    with engine.begin() as connection:
        migrate.upgrade(connection)
        try:
            context = MigrationContext.configure(
                connection,
                opts={
                    "version_table": migrate.VERSION_TABLE,
                    "include_object": lambda obj, name, type_, *_: (
                        type_ != "table" or name in Base.metadata.tables
                    ),
                },
            )
            assert compare_metadata(context, Base.metadata) == []
        finally:
            migrate.downgrade(connection)


def test_the_database_refuses_updates_and_deletes(session):
    session.execute(
        text(
            "INSERT INTO quality_events (kind, instrument, at, note, by_id, by) "
            "VALUES (1, 'ICP-1', '2026-10-01 00:00:00', 'cones', 'q', '{}')"
        )
    )
    for statement in ("UPDATE quality_events SET note = 'x'", "DELETE FROM quality_events"):
        with pytest.raises(DBAPIError), session.begin_nested():
            session.execute(text(statement))
