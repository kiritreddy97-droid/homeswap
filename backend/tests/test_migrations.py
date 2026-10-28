from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from alembic import command
from app import models  # noqa: F401
from app.db import Base

BACKEND = Path(__file__).resolve().parent.parent


def _config(engine_url_conn) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["connection"] = engine_url_conn
    return cfg


def test_migrations_match_models_and_roundtrip(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/m.db")
    with engine.begin() as conn:
        command.upgrade(_config(conn), "head")
        # the migrated schema has no drift from the SQLAlchemy models
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
        assert diff == []
        assert {"users", "homes", "swaps", "points_ledger", "otp_codes", "reviews", "damage_claims"} <= set(
            inspect(conn).get_table_names()
        )
        command.downgrade(_config(conn), "base")
        assert inspect(conn).get_table_names() == ["alembic_version"]
        command.upgrade(_config(conn), "head")


def test_app_does_not_create_tables_itself(tmp_path, monkeypatch):
    """Schema comes only from Alembic; booting the app against an empty DB must not create tables."""
    from fastapi.testclient import TestClient

    from app import main
    from app.db import get_db

    engine = create_engine(f"sqlite:///{tmp_path}/empty.db")
    main.app.dependency_overrides[get_db] = lambda: iter(())
    try:
        with TestClient(main.app):
            pass
    finally:
        main.app.dependency_overrides.clear()
    assert inspect(engine).get_table_names() == []
