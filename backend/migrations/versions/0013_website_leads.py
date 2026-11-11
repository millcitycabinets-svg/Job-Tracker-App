"""Deduplicate website inquiries and retain a durable synchronization cursor."""
from alembic import op
import sqlalchemy as sa
revision='0013'
down_revision='0012'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('workspace_website_leads',sa.Column('source_id',sa.String(36),primary_key=True),sa.Column('source_sequence',sa.BigInteger(),nullable=False,unique=True),sa.Column('source_created_at',sa.BigInteger(),nullable=False),sa.Column('opportunity_id',sa.Integer(),sa.ForeignKey('workspace_opportunities.id'),nullable=False,unique=True))
    op.create_index('ix_workspace_website_leads_source_created_at','workspace_website_leads',['source_created_at'])

def downgrade():
    op.drop_table('workspace_website_leads')
