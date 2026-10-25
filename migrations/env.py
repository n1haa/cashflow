from alembic import context
from backend.config import Settings
from backend.database import Base, make_engine
from backend import models  # noqa: F401 -- register metadata

config = context.config
url = config.attributes.get("database_url") or Settings.from_env().database_url

if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
