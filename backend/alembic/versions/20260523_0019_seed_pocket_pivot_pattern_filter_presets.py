"""Seed Pocket Pivot / Power Trend / SE-pattern predefined filter presets.

Follow-up to ``20260503_0018_seed_predefined_filter_presets`` covering the
nine predefined screens added to the static site in the Pocket Pivot /
Power Trend / SE Pattern / Up-Down Volume PR. The original seed migration
predated this PR, so live deployments never received these starter rows.

Structure mirrors ``20260503_0018``:

* Definitions are embedded inline so the migration is stable even if
  ``preset_screens.py`` is later refactored.
* ``upgrade()`` skips any name that already exists (e.g. a user manually
  created their own "Power Trend") and records the freshly-inserted ids
  in a private audit table.
* ``downgrade()`` deletes only rows whose primary key is in the audit
  table AND whose ``name`` / ``description`` / ``filters`` / ``sort_by``
  / ``sort_order`` still match the seed values — so user edits to a
  seeded row are preserved.

The audit table is namespaced (``_seed_predefined_filter_presets_v2_audit``)
to avoid collision with the prior migration's audit table.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op


revision = "20260523_0019"
down_revision = "20260503_0018"
branch_labels = None
depends_on = None


def _empty_filter_shape() -> dict[str, Any]:
    """Mirror ``buildDefaultScanFilters()`` in defaultFilters.js as of this PR."""
    range_filter = {"min": None, "max": None}
    return {
        "symbolSearch": "",
        "stage": None,
        "ratings": [],
        "ibdIndustries": {"values": [], "mode": "include"},
        "gicsSectors": {"values": [], "mode": "include"},
        "minVolume": None,
        "minMarketCap": None,
        "marketCapUsd": dict(range_filter),
        "advUsd": dict(range_filter),
        "markets": [],
        "compositeScore": dict(range_filter),
        "minerviniScore": dict(range_filter),
        "canslimScore": dict(range_filter),
        "ipoScore": dict(range_filter),
        "customScore": dict(range_filter),
        "volBreakthroughScore": dict(range_filter),
        "seSetupScore": dict(range_filter),
        "seDistanceToPivot": dict(range_filter),
        "seBbSqueeze": dict(range_filter),
        "seVolumeVs50d": dict(range_filter),
        "seUpDownVolume": dict(range_filter),
        "sePatternPrimary": [],
        "seSetupReady": None,
        "seRsLineNewHigh": None,
        "rsRating": dict(range_filter),
        "rs1m": dict(range_filter),
        "rs3m": dict(range_filter),
        "rs12m": dict(range_filter),
        "epsRating": dict(range_filter),
        "price": dict(range_filter),
        "adrPercent": dict(range_filter),
        "epsGrowth": dict(range_filter),
        "salesGrowth": dict(range_filter),
        "vcpScore": dict(range_filter),
        "vcpPivot": dict(range_filter),
        "vcpDetected": None,
        "vcpReady": None,
        "maAlignment": None,
        "passesTemplate": None,
        "pocketPivot": None,
        "powerTrend": None,
        "perfDay": dict(range_filter),
        "perfWeek": dict(range_filter),
        "perfMonth": dict(range_filter),
        "perf3m": dict(range_filter),
        "perf6m": dict(range_filter),
        "gapPercent": dict(range_filter),
        "volumeSurge": dict(range_filter),
        "ema10Distance": dict(range_filter),
        "ema20Distance": dict(range_filter),
        "ema50Distance": dict(range_filter),
        "week52HighDistance": dict(range_filter),
        "week52LowDistance": dict(range_filter),
        "ipoAfter": None,
        "beta": dict(range_filter),
        "betaAdjRs": dict(range_filter),
    }


# (name, description, sparse_filter_overrides, sort_by, sort_order)
SEEDED_PRESETS: list[tuple[str, str, dict[str, Any], str, str]] = [
    (
        "Pocket Pivot",
        "Up-day volume exceeding every down day of the prior 10 sessions.",
        {
            "pocketPivot": True,
            "rsRating": {"min": 70, "max": None},
        },
        "volume_surge",
        "desc",
    ),
    (
        "Power Trend",
        "Minervini Power Trend: price riding the 21-EMA above a rising 50-SMA.",
        {
            "powerTrend": True,
            "rsRating": {"min": 80, "max": None},
        },
        "rs_rating",
        "desc",
    ),
    (
        "Under Accumulation",
        "Strong 10-day up/down volume ratio signalling institutional buying.",
        {
            "seUpDownVolume": {"min": 1.5, "max": None},
            "rsRating": {"min": 80, "max": None},
            "stage": 2,
        },
        "se_up_down_volume_ratio_10d",
        "desc",
    ),
    (
        "Cup with Handle",
        "Setup Engine cup-with-handle base detections in strong stocks.",
        {
            "sePatternPrimary": ["cup_with_handle"],
            "rsRating": {"min": 70, "max": None},
        },
        "se_setup_score",
        "desc",
    ),
    (
        "Double Bottom",
        "Setup Engine double-bottom base detections in strong stocks.",
        {
            "sePatternPrimary": ["double_bottom"],
            "rsRating": {"min": 70, "max": None},
        },
        "se_setup_score",
        "desc",
    ),
    (
        "High Tight Flag",
        "Setup Engine high-tight-flag detections — explosive momentum bases.",
        {
            "sePatternPrimary": ["high_tight_flag"],
            "rsRating": {"min": 80, "max": None},
        },
        "se_setup_score",
        "desc",
    ),
    (
        "First Pullback",
        "Setup Engine first-pullback detections after a breakout.",
        {
            "sePatternPrimary": ["first_pullback"],
            "rsRating": {"min": 70, "max": None},
        },
        "se_setup_score",
        "desc",
    ),
    (
        "Three Weeks Tight",
        "Setup Engine three-weeks-tight continuation detections.",
        {
            "sePatternPrimary": ["three_weeks_tight"],
            "rsRating": {"min": 70, "max": None},
        },
        "se_setup_score",
        "desc",
    ),
    (
        "NR7 / Inside Day",
        "Setup Engine NR7 / inside-day volatility-contraction triggers.",
        {
            "sePatternPrimary": ["nr7_inside_day"],
            "rsRating": {"min": 70, "max": None},
        },
        "se_setup_score",
        "desc",
    ),
]


_AUDIT_TABLE_NAME = "_seed_predefined_filter_presets_v2_audit"


def _filter_presets_table() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "filter_presets",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String),
        sa.Column("description", sa.Text),
        sa.Column("filters", sa.Text),
        sa.Column("sort_by", sa.String),
        sa.Column("sort_order", sa.String),
        sa.Column("position", sa.Integer),
    )


def _audit_table_ref() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        _AUDIT_TABLE_NAME,
        metadata,
        sa.Column("filter_preset_id", sa.Integer, primary_key=True),
    )


def _build_filters_payload(overrides: dict[str, Any]) -> str:
    payload = _empty_filter_shape()
    payload.update(overrides)
    return json.dumps(payload)


def upgrade() -> None:
    bind = op.get_bind()
    table = _filter_presets_table()

    existing_names = {
        row[0]
        for row in bind.execute(sa.select(table.c.name)).fetchall()
    }
    next_position = bind.execute(
        sa.select(sa.func.coalesce(sa.func.max(table.c.position), -1))
    ).scalar_one() + 1

    rows_to_insert: list[dict[str, Any]] = []
    for name, description, overrides, sort_by, sort_order in SEEDED_PRESETS:
        if name in existing_names:
            continue
        rows_to_insert.append(
            {
                "name": name,
                "description": description,
                "filters": _build_filters_payload(overrides),
                "sort_by": sort_by,
                "sort_order": sort_order,
                "position": next_position,
            }
        )
        next_position += 1

    inspector = sa.inspect(bind)
    if not inspector.has_table(_AUDIT_TABLE_NAME):
        op.create_table(
            _AUDIT_TABLE_NAME,
            sa.Column("filter_preset_id", sa.Integer, primary_key=True),
        )

    if not rows_to_insert:
        return

    audit = _audit_table_ref()
    inserted_ids: list[int] = []
    for row in rows_to_insert:
        result = bind.execute(table.insert().values(**row))
        inserted_ids.append(int(result.inserted_primary_key[0]))

    bind.execute(
        audit.insert(),
        [{"filter_preset_id": new_id} for new_id in inserted_ids],
    )


def downgrade() -> None:
    """Remove only rows ``upgrade()`` inserted that the user has not edited."""
    bind = op.get_bind()
    table = _filter_presets_table()

    inspector = sa.inspect(bind)
    if not inspector.has_table(_AUDIT_TABLE_NAME):
        return

    audit = _audit_table_ref()
    inserted_ids = [
        row[0]
        for row in bind.execute(sa.select(audit.c.filter_preset_id)).fetchall()
    ]

    if inserted_ids:
        for name, description, overrides, sort_by, sort_order in SEEDED_PRESETS:
            bind.execute(
                table.delete().where(
                    sa.and_(
                        table.c.id.in_(inserted_ids),
                        table.c.name == name,
                        table.c.description == description,
                        table.c.filters == _build_filters_payload(overrides),
                        table.c.sort_by == sort_by,
                        table.c.sort_order == sort_order,
                    )
                )
            )

    op.drop_table(_AUDIT_TABLE_NAME)
