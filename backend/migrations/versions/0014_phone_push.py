"""Opt-in encrypted device subscriptions and durable push cursors."""
from alembic import op
import sqlalchemy as sa
revision='0014'
down_revision='0013'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('workspace_push_config',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('secret',sa.Text(),nullable=False),sa.Column('public_key',sa.Text(),nullable=False))
    op.create_table('workspace_push_devices',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('endpoint_hash',sa.String(64),unique=True,nullable=False),sa.Column('encrypted_subscription',sa.Text(),nullable=False),sa.Column('last_sequence',sa.BigInteger(),nullable=False))
    op.create_index('ix_workspace_push_devices_user_id','workspace_push_devices',['user_id'])

def downgrade():
    op.drop_table('workspace_push_devices')
    op.drop_table('workspace_push_config')
