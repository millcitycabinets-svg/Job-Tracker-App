"""Source identity and provenance for additive Monday imports."""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workspace_monday_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("board_id", sa.String(64), nullable=False),
        sa.Column("item_id", sa.String(64), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("workspace_opportunities.id"), nullable=False, unique=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_group_id", sa.String(100), nullable=False),
        sa.Column("source_group_title", sa.String(200), nullable=False),
        sa.Column("source_status", sa.String(100), nullable=False),
        sa.Column("source_updated_at", sa.String(64)),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("owner_review", sa.Boolean(), nullable=False),
        sa.Column("sales_review", sa.Boolean(), nullable=False),
        sa.Column("review_reason", sa.Text()),
        sa.Column("import_target", sa.String(200), nullable=False),
        sa.Column("backup_reference", sa.Text(), nullable=False),
        sa.Column("imported_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("board_id", "item_id", name="uq_workspace_monday_source"),
    )


def downgrade():
    op.drop_table("workspace_monday_sources")
