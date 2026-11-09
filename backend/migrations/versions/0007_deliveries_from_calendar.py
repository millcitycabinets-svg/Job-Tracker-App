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
    # Batch mode preserves the same PostgreSQL ALTER statements and supports the
    # isolated SQLite pilot by recreating this table when ALTER is unavailable.
    with op.batch_alter_table('deliveries') as batch:
        batch.alter_column('job_id', existing_type=sa.Integer(), nullable=True)
        batch.alter_column('driver_id', existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column('title', sa.String(length=300), nullable=True))
        batch.add_column(sa.Column('calendar_event_id', sa.String(length=300), nullable=True))
        batch.create_unique_constraint('deliveries_calendar_event_id_key', ['calendar_event_id'])


def downgrade():
    with op.batch_alter_table('deliveries') as batch:
        batch.drop_constraint('deliveries_calendar_event_id_key', type_='unique')
        batch.drop_column('calendar_event_id')
        batch.drop_column('title')
