from alembic import context
from sqlalchemy import create_engine

from rtagent.config import Settings

config = context.config
url = config.get_main_option("sqlalchemy.url")
if url == "sqlite:///var/app.db":
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
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
