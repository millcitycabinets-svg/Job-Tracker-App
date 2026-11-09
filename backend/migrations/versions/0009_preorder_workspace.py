"""Pre-order operations stages, separate from sales revisions and financial jobs."""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    # Inline checks permit ADD COLUMN on SQLite and PostgreSQL. Rebuilding this
    # parent table would violate existing request/activity foreign keys on SQLite.
    op.add_column("workspace_opportunities", sa.Column("operations_stage", sa.String(30),
        sa.CheckConstraint("operations_stage IN ('Prepping', 'Ready to Order')", name="ck_workspace_preorder_stage"),
        nullable=False, server_default="Prepping"))
    op.add_column("workspace_opportunities", sa.Column("operations_due_date", sa.Date(), nullable=True))
    op.add_column("workspace_opportunities", sa.Column("operations_revision", sa.Integer(),
        sa.CheckConstraint("operations_revision > 0", name="ck_workspace_preorder_revision"),
        nullable=False, server_default="1"))
    op.create_index("ix_workspace_preorder_stage", "workspace_opportunities", ["stage", "linked_job_id", "operations_stage"])


def downgrade():
    op.drop_index("ix_workspace_preorder_stage", table_name="workspace_opportunities")
    # SQLite >=3.35 and PostgreSQL remove each inline column check with its column.
    op.drop_column("workspace_opportunities", "operations_revision")
    op.drop_column("workspace_opportunities", "operations_due_date")
    op.drop_column("workspace_opportunities", "operations_stage")
