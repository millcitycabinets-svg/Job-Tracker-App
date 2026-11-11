"""Job-linked customer review recordings."""
from alembic import op
import sqlalchemy as sa
revision='0017'
down_revision='0016'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('workspace_review_recordings',sa.Column('id',sa.String(36),primary_key=True),sa.Column('notebook_id',sa.String(36),sa.ForeignKey('workspace_notebooks.id'),nullable=False),sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('title',sa.String(200),nullable=False),sa.Column('content_type',sa.String(80),nullable=False),sa.Column('content',sa.LargeBinary(),nullable=False),sa.Column('digest',sa.String(64),nullable=False),sa.Column('size',sa.Integer(),nullable=False),sa.Column('duration',sa.Integer(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('consent_confirmed',sa.Boolean(),nullable=False))
    op.create_index('ix_workspace_review_recordings_notebook_id','workspace_review_recordings',['notebook_id'])
def downgrade():op.drop_table('workspace_review_recordings')
