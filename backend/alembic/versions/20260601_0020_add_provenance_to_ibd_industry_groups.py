"""Add provenance columns to ibd_industry_groups for the hybrid classifier.

The table was US-only and populated solely from the curated CSV. The hybrid IBD
classifier now also fills symbols/markets the CSV doesn't cover, so each row needs
to record how it was assigned (``source``), how confident the classifier was
(``confidence``), the finer method tag (``method``), the model identifier
(``model_version``), and which market it belongs to (``market``).

Backfill strategy mirrors ``20260424_0015_add_market_to_ibd_group_ranks``: add the
NOT NULL columns with a server_default so existing rows fill cleanly, then drop the
server_default so every future write must set ``source``/``market`` explicitly —
a silent default of ``csv`` would corrupt the authoritative-override invariant.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260601_0020"
down_revision = "20260529_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add columns. NOT NULL columns get a server_default so existing rows
    #    (all curated CSV/US) backfill without a rewrite.
    op.add_column(
        "ibd_industry_groups",
        sa.Column("market", sa.String(length=8), nullable=False, server_default="US"),
    )
    op.add_column(
        "ibd_industry_groups",
        sa.Column("source", sa.String(length=20), nullable=False, server_default="csv"),
    )
    op.add_column(
        "ibd_industry_groups",
        sa.Column("confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "ibd_industry_groups",
        sa.Column("method", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "ibd_industry_groups",
        sa.Column("model_version", sa.String(length=80), nullable=True),
    )

    op.create_index(
        "idx_ibd_industry_group_market", "ibd_industry_groups", ["market"]
    )
    op.create_index(
        "idx_ibd_industry_group_source", "ibd_industry_groups", ["source"]
    )

    # 2. Drop the server_defaults now that existing rows are backfilled — new rows
    #    must set market/source explicitly so classifier rows can never be silently
    #    mislabelled as authoritative ``csv``.
    with op.batch_alter_table("ibd_industry_groups") as batch_op:
        batch_op.alter_column("market", server_default=None)
        batch_op.alter_column("source", server_default=None)


def downgrade() -> None:
    op.drop_index("idx_ibd_industry_group_source", table_name="ibd_industry_groups")
    op.drop_index("idx_ibd_industry_group_market", table_name="ibd_industry_groups")
    with op.batch_alter_table("ibd_industry_groups") as batch_op:
        batch_op.drop_column("model_version")
        batch_op.drop_column("method")
        batch_op.drop_column("confidence")
        batch_op.drop_column("source")
        batch_op.drop_column("market")
