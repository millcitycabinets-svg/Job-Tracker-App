"""Add isolated communication records and encrypted mailbox grants."""
from alembic import op
import sqlalchemy as sa
revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('workspace_mailboxes', sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True), sa.Column('email', sa.String(320), nullable=False, unique=True), sa.Column('encrypted_token', sa.Text(), nullable=False), sa.Column('connected_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('workspace_oauth_attempts', sa.Column('state_hash', sa.String(64), primary_key=True), sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False), sa.Column('expires', sa.Integer(), nullable=False))
    op.create_table('workspace_communications', sa.Column('id', sa.Integer(), primary_key=True), sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False), sa.Column('opportunity_id', sa.Integer(), sa.ForeignKey('workspace_opportunities.id'), nullable=False), sa.Column('command_id', sa.String(160), nullable=False), sa.Column('channel', sa.String(20), nullable=False), sa.Column('direction', sa.String(20), nullable=False), sa.Column('recipient', sa.String(320), nullable=False), sa.Column('subject', sa.String(200), nullable=False), sa.Column('body', sa.Text(), nullable=False), sa.Column('status', sa.String(30), nullable=False), sa.Column('provider_id', sa.String(200), unique=True), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint('actor_id','command_id',name='uq_communication_command'))
    op.create_index('ix_workspace_communications_actor_id','workspace_communications',['actor_id'])
    op.create_index('ix_workspace_communications_opportunity_id','workspace_communications',['opportunity_id'])
    op.create_table('workspace_contact_permissions', sa.Column('id',sa.Integer(),primary_key=True), sa.Column('channel',sa.String(20),nullable=False), sa.Column('address',sa.String(320),nullable=False), sa.Column('status',sa.String(20),nullable=False), sa.Column('evidence',sa.Text(),nullable=False), sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id')), sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('channel','address',name='uq_contact_permission'))
    op.create_table('workspace_campaigns',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('name',sa.String(200),nullable=False),sa.Column('audience',sa.String(20),nullable=False),sa.Column('subject',sa.String(200),nullable=False),sa.Column('body',sa.Text(),nullable=False),sa.Column('opportunity_ids',sa.JSON(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_workspace_campaigns_actor_id','workspace_campaigns',['actor_id'])


def downgrade():
    for table in ('workspace_campaigns','workspace_contact_permissions','workspace_communications','workspace_oauth_attempts','workspace_mailboxes'):
        op.drop_table(table)
