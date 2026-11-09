"""Add the Mill City sales and operations workspace without changing finance tables."""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("workspace_opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("customer", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320)), sa.Column("phone", sa.String(80)),
        sa.Column("address", sa.String(500)),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2)), sa.Column("next_follow_up", sa.Date()),
        sa.Column("work_scope", sa.Text()), sa.Column("notes", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("linked_job_id", sa.Integer(), sa.ForeignKey("jobs.id"), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("visibility IN ('private', 'shared')", name="ck_workspace_visibility"),
        sa.CheckConstraint("revision > 0", name="ck_workspace_opportunity_revision"),
        sa.CheckConstraint("amount IS NULL OR amount >= 0", name="ck_workspace_amount"))
    op.create_index("ix_workspace_opportunity_owner_stage", "workspace_opportunities", ["owner_id", "stage"])
    op.create_index("ix_workspace_opportunity_visibility", "workspace_opportunities", ["visibility"])
    op.create_table("workspace_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("workspace_opportunities.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False), sa.Column("status", sa.String(30), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("assigned_to_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("due_date", sa.Date()), sa.Column("result", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("opportunity_id", "kind", "version", name="uq_workspace_request_version"),
        sa.CheckConstraint("kind IN ('Design', 'Estimate')", name="ck_workspace_request_kind"),
        sa.CheckConstraint("status IN ('Queued', 'In Progress', 'Needs Information', 'Complete')", name="ck_workspace_request_status"),
        sa.CheckConstraint("version > 0 AND revision > 0", name="ck_workspace_request_revision"))
    op.create_index("uq_workspace_open_request", "workspace_requests", ["opportunity_id", "kind"], unique=True,
                    postgresql_where=sa.text("status <> 'Complete'"), sqlite_where=sa.text("status <> 'Complete'"))
    op.create_index("ix_workspace_request_status_kind", "workspace_requests", ["status", "kind"])
    op.create_table("workspace_activities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("workspace_opportunities.id"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False), sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_workspace_activity_opportunity_created", "workspace_activities", ["opportunity_id", "created_at"])
    op.create_table("workspace_commands",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("command_id", sa.String(160), nullable=False, unique=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("actor_role", sa.String(20), nullable=False), sa.Column("operation", sa.String(60), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False), sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("workspace_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("settings", sa.JSON(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    for name in ("workspace_preferences", "workspace_commands", "workspace_activities", "workspace_requests", "workspace_opportunities"):
        op.drop_table(name)
