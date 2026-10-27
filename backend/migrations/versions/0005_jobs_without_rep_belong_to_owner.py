"""jobs with no rep become the owner's own sales

Revision ID: 0005
Revises: 0004
"""
from alembic import op


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE jobs SET rep_id = (SELECT id FROM users WHERE role = 'owner' AND active ORDER BY id LIMIT 1)
        WHERE rep_id IS NULL
    """)


def downgrade():
    pass  # can't tell which jobs were blank before
