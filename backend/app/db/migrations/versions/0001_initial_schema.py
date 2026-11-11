"""Initial schema of the demo core.

Revision ID: 0001
Revises:
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("brand_raw", sa.Text(), nullable=False),
        sa.Column("article_raw", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("article", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("purchase_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("min_markup_percent", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("step", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("ceiling", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("quarantine_threshold_percent", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.CheckConstraint("article <> ''", name=op.f("ck_products_article_not_empty")),
        sa.CheckConstraint("brand <> ''", name=op.f("ck_products_brand_not_empty")),
        sa.CheckConstraint(
            "min_markup_percent >= 0", name=op.f("ck_products_min_markup_percent_non_negative")
        ),
        sa.CheckConstraint(
            "quarantine_threshold_percent >= 0",
            name=op.f("ck_products_quarantine_threshold_percent_non_negative"),
        ),
        sa.CheckConstraint("step >= 0", name=op.f("ck_products_step_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("brand", "article", name=op.f("uq_products_brand_article")),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "running", "succeeded", "partial", "failed", name="run_status"),
            nullable=False,
        ),
        sa.Column("trigger", sa.Enum("manual", "schedule", name="run_trigger"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finish_reason", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "(status IN ('queued', 'running')) = (finished_at IS NULL)",
            name=op.f("ck_runs_finished_at_matches_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
    )
    op.create_index(
        "uq_runs_single_active",
        "runs",
        [sa.literal_column("(true)")],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "calculations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("rules_version", sa.String(length=32), nullable=False),
        sa.Column("purchase_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("min_markup_percent", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("step", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("ceiling", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("quarantine_threshold_percent", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("suspicious_price_factor", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("max_delivery_days", sa.Integer(), nullable=False),
        sa.Column("previous_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("partial_data", sa.Boolean(), nullable=False),
        sa.Column("accepted_offers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rejected_offers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("min_competitor_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("target_price_raw", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("target_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("lower_bound", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("upper_bound", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("applied_constraints", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recommended_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column(
            "state",
            sa.Enum("published", "unchanged", "quarantine", "retained", name="result_state"),
            nullable=False,
        ),
        sa.Column(
            "reason",
            sa.Enum(
                "no_previous_price",
                "price_spike",
                "no_eligible_offers",
                "invalid_purchase_price",
                "rules_conflict",
                name="result_reason",
            ),
            nullable=True,
        ),
        sa.Column("change_percent", sa.Numeric(), nullable=True),
        sa.Column("change_percent_display", sa.Numeric(), nullable=True),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(state = 'retained') = (recommended_price IS NULL)",
            name=op.f("ck_calculations_recommended_price_matches_state"),
        ),
        sa.CheckConstraint(
            "(state IN ('published', 'unchanged')) = (reason IS NULL)",
            name=op.f("ck_calculations_reason_matches_state"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_calculations_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_calculations_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calculations")),
        sa.UniqueConstraint("run_id", "product_id", name=op.f("uq_calculations_run_id_product_id")),
    )
    op.create_index("ix_calculations_product_id", "calculations", ["product_id"], unique=False)
    op.create_table(
        "offers",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Enum("html_catalog", "json_api", name="source"), nullable=False),
        sa.Column("brand_raw", sa.Text(), nullable=False),
        sa.Column("article_raw", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("article", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.Column("delivery_days", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("delivery_days >= 0", name=op.f("ck_offers_delivery_days_non_negative")),
        sa.CheckConstraint("stock >= 0", name=op.f("ck_offers_stock_non_negative")),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_offers_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_offers_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offers")),
    )
    op.create_index("ix_offers_product_id", "offers", ["product_id"], unique=False)
    op.create_index(
        "ix_offers_run_id_brand_article", "offers", ["run_id", "brand", "article"], unique=False
    )
    op.create_table(
        "source_results",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Enum("html_catalog", "json_api", name="source"), nullable=False),
        sa.Column("status", sa.Enum("ok", "failed", name="source_status"), nullable=False),
        sa.Column(
            "error_kind",
            sa.Enum("network", "structure", "blocked", name="source_error_kind"),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("offer_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status = 'ok') = (error_kind IS NULL)",
            name=op.f("ck_source_results_error_kind_matches_status"),
        ),
        sa.CheckConstraint(
            "offer_count >= 0", name=op.f("ck_source_results_offer_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_source_results_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_results")),
        sa.UniqueConstraint("run_id", "source", name=op.f("uq_source_results_run_id_source")),
    )
    op.create_table(
        "published_prices",
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculation_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("price > 0", name=op.f("ck_published_prices_price_positive")),
        sa.ForeignKeyConstraint(
            ["calculation_id"],
            ["calculations.id"],
            name=op.f("fk_published_prices_calculation_id_calculations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_published_prices_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("product_id", name=op.f("pk_published_prices")),
    )


def downgrade() -> None:
    op.drop_table("published_prices")
    op.drop_table("source_results")
    op.drop_index("ix_offers_run_id_brand_article", table_name="offers")
    op.drop_index("ix_offers_product_id", table_name="offers")
    op.drop_table("offers")
    op.drop_index("ix_calculations_product_id", table_name="calculations")
    op.drop_table("calculations")
    op.drop_index(
        "uq_runs_single_active",
        table_name="runs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_table("runs")
    op.drop_table("products")
    # Enum types are not dropped together with the tables.
    for enum_name in (
        "result_reason",
        "result_state",
        "source_error_kind",
        "source_status",
        "source",
        "run_trigger",
        "run_status",
    ):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=False)
