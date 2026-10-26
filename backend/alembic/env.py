from sqlalchemy import create_engine

from alembic import context
from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.db import DATABASE_URL, Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Tests hand in a ready connection; the CLI builds one from DATABASE_URL.
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        _run(conn)


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
