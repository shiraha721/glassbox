from alembic import context
from sqlalchemy import create_engine

from rtagent.infrastructure.config import Settings

config = context.config
url = config.get_main_option("sqlalchemy.url")
if url is None:  # the alembic command line: the database in the configured data folder
    settings = Settings()
    settings.initialize()
    url = settings.database_url.replace("+aiosqlite", "")

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url).connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
