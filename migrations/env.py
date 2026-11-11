from alembic import context

from tariff_agent.config import Settings
from tariff_agent.persistence import database_target, engine_for, metadata

settings = Settings()
with engine_for(database_target(settings)).connect() as connection:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
