from alembic import context

from app import website_leads, communication_models, models, workspace_models, contact_import_models, monday_import  # noqa: F401
from app.db import Base, engine

def run_migrations(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


provided_connection = context.config.attributes.get("connection")
if provided_connection is not None:
    # The caller owns its transaction (including rollback-only rehearsals).
    if not provided_connection.in_transaction():
        raise RuntimeError("A supplied migration connection requires an active caller-owned transaction")
    run_migrations(provided_connection)
else:
    with engine.connect() as connection:
        run_migrations(connection)
        connection.commit()
