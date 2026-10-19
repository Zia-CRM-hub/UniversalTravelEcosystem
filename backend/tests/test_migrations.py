import tempfile
from pathlib import Path

import sqlalchemy as sa

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.db import Base

BACKEND = Path(__file__).resolve().parents[1]


def test_migrations_apply_and_match_models():
    """Fails when someone changes models.py without adding a migration."""
    path = Path(tempfile.mkdtemp()) / "mig.db"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path.as_posix()}")
    command.upgrade(cfg, "head")

    eng = sa.create_engine(f"sqlite:///{path.as_posix()}")
    with eng.connect() as conn:
        assert {"users", "bookings", "offer_snapshots", "agent_sessions"} <= set(sa.inspect(conn).get_table_names())
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models drifted from migrations: {diff}"
