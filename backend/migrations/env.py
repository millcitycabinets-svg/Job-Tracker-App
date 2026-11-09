from alembic import context

from app import models, workspace_models  # noqa: F401  (registers every table on Base.metadata)
from app.db import Base, engine

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
