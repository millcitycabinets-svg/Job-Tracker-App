"""Persist contact CSV previews, import receipts, and source provenance."""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("workspace_contact_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("creator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("mapping", sa.JSON(), nullable=False),
        sa.Column("suggested_mapping", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('preview', 'committed')", name="ck_contact_import_status"),
        sa.CheckConstraint("visibility IN ('private', 'shared')", name="ck_contact_import_visibility"))
    op.create_index("ix_contact_import_creator_created", "workspace_contact_imports", ["creator_id", "created_at"])
    op.create_table("workspace_contact_import_rows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("workspace_contact_imports.id"), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("duplicate_reason", sa.String(200)),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("workspace_opportunities.id"), unique=True),
        sa.Column("source_kind", sa.String(20), nullable=False, server_default="csv"),
        sa.Column("lead_kind", sa.String(20), nullable=False, server_default="prospect"),
        sa.Column("marketing_consent", sa.String(30), nullable=False, server_default="not_recorded"),
        sa.UniqueConstraint("batch_id", "row_number", name="uq_contact_import_row"),
        sa.CheckConstraint("status IN ('valid', 'duplicate', 'invalid', 'created')", name="ck_contact_import_row_status"))
    op.create_index("ix_contact_import_row_opportunity", "workspace_contact_import_rows", ["opportunity_id"])
    op.create_table("workspace_contact_import_lock",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("id = 1", name="ck_contact_import_lock_singleton"))
    op.bulk_insert(sa.table("workspace_contact_import_lock", sa.column("id", sa.Integer()),
                           sa.column("revision", sa.Integer())), [{"id": 1, "revision": 0}])


def downgrade():
    op.drop_table("workspace_contact_import_rows")
    op.drop_table("workspace_contact_imports")
    op.drop_table("workspace_contact_import_lock")
