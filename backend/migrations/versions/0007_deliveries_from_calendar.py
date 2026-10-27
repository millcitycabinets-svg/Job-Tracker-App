"""deliveries can come from the DELIVERY calendar: optional job and driver, event id and title

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('deliveries', 'job_id', existing_type=sa.Integer(), nullable=True)
    op.alter_column('deliveries', 'driver_id', existing_type=sa.Integer(), nullable=True)
    op.add_column('deliveries', sa.Column('title', sa.String(length=300), nullable=True))
    op.add_column('deliveries', sa.Column('calendar_event_id', sa.String(length=300), nullable=True))
    op.create_unique_constraint('deliveries_calendar_event_id_key', 'deliveries', ['calendar_event_id'])


def downgrade():
    op.drop_constraint('deliveries_calendar_event_id_key', 'deliveries')
    op.drop_column('deliveries', 'calendar_event_id')
    op.drop_column('deliveries', 'title')
