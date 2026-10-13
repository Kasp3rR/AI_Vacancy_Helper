from alembic import context
from sqlalchemy import Connection

from app import models  # noqa: F401
from app.config import get_settings
from app.db import Base, get_engine


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    external_connection = context.config.attributes.get("connection")
    if external_connection is not None:
        migrate(external_connection)
    else:
        with get_engine().connect() as connection:
            migrate(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
