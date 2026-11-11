"""Team library and persistent field notebooks."""
from alembic import op
import sqlalchemy as sa
revision='0015'
down_revision='0014'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('workspace_team_files',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('path',sa.String(500),nullable=False),sa.Column('size',sa.Integer(),nullable=False),sa.Column('content',sa.LargeBinary(),nullable=False),sa.Column('uploaded_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('workspace_notebooks',sa.Column('id',sa.String(36),primary_key=True),sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('opportunity_id',sa.Integer(),sa.ForeignKey('workspace_opportunities.id')),sa.Column('job_id',sa.Integer(),sa.ForeignKey('jobs.id')),sa.Column('title',sa.String(200),nullable=False),sa.Column('content',sa.JSON(),nullable=False),sa.Column('revision',sa.Integer(),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    for column in ('user_id','opportunity_id','job_id'):op.create_index('ix_workspace_notebooks_'+column,'workspace_notebooks',[column])

def downgrade():
    op.drop_table('workspace_notebooks');op.drop_table('workspace_team_files')
