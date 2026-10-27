"""crew pay types (hourly / per box / salary), per-person box rate, deliveries

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('pay_type', sa.String(length=20), server_default='hourly', nullable=False))
    op.add_column('users', sa.Column('box_rate', sa.Numeric(precision=12, scale=2), nullable=True))
    op.create_table(
        'deliveries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('driver_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('delivered_on', sa.Date(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table('deliveries')
    op.drop_column('users', 'box_rate')
    op.drop_column('users', 'pay_type')
