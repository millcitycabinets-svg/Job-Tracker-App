"""Customer-scoped designer and estimator grants."""
from alembic import op
import sqlalchemy as sa
revision='0016'
down_revision='0015'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('workspace_tool_grants',sa.Column('id',sa.String(64),primary_key=True),sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('opportunity_id',sa.Integer(),sa.ForeignKey('workspace_opportunities.id'),nullable=False),sa.Column('credential_version',sa.String(64),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_workspace_tool_grants_user_id','workspace_tool_grants',['user_id'])
def downgrade():op.drop_table('workspace_tool_grants')
