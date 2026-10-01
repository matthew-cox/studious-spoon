from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from shortener_api.settings import load_migrate_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _database_url() -> str:
    # Tests inject the URL via the Alembic config; the release job uses the environment.
    return config.get_main_option("sqlalchemy.url") or str(
        load_migrate_settings().migrator_database_url
    )


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), literal_binds=True, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
