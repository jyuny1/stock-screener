"""Tests for the PR-follow-up filter presets seed migration (0019)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"
MIGRATION_FILENAME = (
    "20260523_0019_seed_pocket_pivot_pattern_filter_presets.py"
)


def _load_migration():
    path = MIGRATIONS_DIR / MIGRATION_FILENAME
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_filter_presets_table(engine: sa.Engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "filter_presets",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("filters", sa.Text, nullable=False),
        sa.Column("sort_by", sa.String(50), nullable=False),
        sa.Column("sort_order", sa.String(10), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
    )
    metadata.create_all(engine)


def _run_upgrade(module, connection) -> None:
    context = MigrationContext.configure(connection)
    module.op = Operations(context)
    module.upgrade()


def _run_downgrade(module, connection) -> None:
    context = MigrationContext.configure(connection)
    module.op = Operations(context)
    module.downgrade()


def test_seed_inserts_all_nine_presets_with_full_filter_shape():
    engine = sa.create_engine("sqlite:///:memory:")
    _make_filter_presets_table(engine)

    migration = _load_migration()
    expected_names = [name for name, *_ in migration.SEEDED_PRESETS]
    assert len(expected_names) == 9

    with engine.begin() as conn:
        _run_upgrade(migration, conn)
        rows = conn.execute(
            sa.text(
                "SELECT name, position, filters FROM filter_presets "
                "ORDER BY position"
            )
        ).fetchall()

    engine.dispose()

    assert [row[0] for row in rows] == expected_names
    assert [row[1] for row in rows] == list(range(len(expected_names)))

    expected_keys = set(migration._empty_filter_shape().keys())
    for _, _, filters_json in rows:
        parsed = json.loads(filters_json)
        assert set(parsed.keys()) == expected_keys


def test_empty_shape_carries_new_pr_keys():
    """The migration must store the keys this PR added so loading a seeded
    preset doesn't drop ``powerTrend`` / ``pocketPivot`` etc. on the way back
    into the FilterPanel."""
    migration = _load_migration()
    shape = migration._empty_filter_shape()
    assert shape["pocketPivot"] is None
    assert shape["powerTrend"] is None
    assert shape["seUpDownVolume"] == {"min": None, "max": None}
    assert shape["sePatternPrimary"] == []


def test_power_trend_preset_overrides():
    migration = _load_migration()
    overrides_by_name = {
        name: overrides for name, _desc, overrides, *_ in migration.SEEDED_PRESETS
    }

    power = overrides_by_name["Power Trend"]
    assert power == {"powerTrend": True, "rsRating": {"min": 80, "max": None}}

    pocket = overrides_by_name["Pocket Pivot"]
    assert pocket == {"pocketPivot": True, "rsRating": {"min": 70, "max": None}}

    cup = overrides_by_name["Cup with Handle"]
    assert cup["sePatternPrimary"] == ["cup_with_handle"]


def test_seed_skips_presets_whose_names_already_exist():
    engine = sa.create_engine("sqlite:///:memory:")
    _make_filter_presets_table(engine)

    migration = _load_migration()
    user_payload = json.dumps({"customized": True})
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO filter_presets "
                "(name, description, filters, sort_by, sort_order, position) "
                "VALUES "
                "('Power Trend', 'user copy', :filters, 'composite_score', 'desc', 0)"
            ),
            {"filters": user_payload},
        )
        _run_upgrade(migration, conn)

        existing = conn.execute(
            sa.text(
                "SELECT filters FROM filter_presets WHERE name = 'Power Trend'"
            )
        ).scalar_one()
        total = conn.execute(
            sa.text("SELECT COUNT(*) FROM filter_presets")
        ).scalar_one()

    engine.dispose()

    assert existing == user_payload
    assert total == 1 + (len(migration.SEEDED_PRESETS) - 1)


def test_seed_is_idempotent_when_run_twice():
    engine = sa.create_engine("sqlite:///:memory:")
    _make_filter_presets_table(engine)

    migration = _load_migration()
    with engine.begin() as conn:
        _run_upgrade(migration, conn)
        first = conn.execute(
            sa.text("SELECT COUNT(*) FROM filter_presets")
        ).scalar_one()
        _run_upgrade(migration, conn)
        second = conn.execute(
            sa.text("SELECT COUNT(*) FROM filter_presets")
        ).scalar_one()

    engine.dispose()
    assert first == len(migration.SEEDED_PRESETS)
    assert second == first


def test_downgrade_removes_seeded_only_and_preserves_user_edits():
    engine = sa.create_engine("sqlite:///:memory:")
    _make_filter_presets_table(engine)

    migration = _load_migration()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO filter_presets "
                "(name, description, filters, sort_by, sort_order, position) "
                "VALUES "
                "('User Custom', NULL, '{}', 'composite_score', 'desc', 0)"
            )
        )
        _run_upgrade(migration, conn)

        # User tightens Power Trend's RS floor — should now survive downgrade.
        edited = migration._empty_filter_shape()
        edited.update(
            {"powerTrend": True, "rsRating": {"min": 95, "max": None}}
        )
        conn.execute(
            sa.text(
                "UPDATE filter_presets SET filters = :filters "
                "WHERE name = 'Power Trend'"
            ),
            {"filters": json.dumps(edited)},
        )

        _run_downgrade(migration, conn)
        remaining = {
            row[0]
            for row in conn.execute(
                sa.text("SELECT name FROM filter_presets")
            ).fetchall()
        }

    engine.dispose()

    assert "User Custom" in remaining
    assert "Power Trend" in remaining  # user-edited
    # Untouched seeded rows are removed.
    assert "Pocket Pivot" not in remaining
    assert "Cup with Handle" not in remaining
