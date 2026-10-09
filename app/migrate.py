"""
Runs Alembic migrations from inside the app, so `uvicorn app.main:app` is
all it takes to get an up-to-date schema.

- A Postgres advisory lock makes this safe when several coordinator
  instances start at once: one migrates, the others wait, then find nothing
  to do.
- A database created before migrations existed (tables present, no
  alembic_version) is stamped at the baseline first, since its tables
  already match 0001_baseline; then the newer migrations apply on top.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, pool, text

from app.config import settings

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
BASELINE = "0001_baseline"
LOCK_KEY = 7_212_026  # arbitrary, constant: identifies "Control Tower migrations"


def alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["configure_logging"] = False
    return cfg


def run_migrations(database_url: str | None = None) -> None:
    engine = create_engine(database_url or settings.database_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
            conn.commit()
            try:
                cfg = alembic_config()
                cfg.attributes["connection"] = conn
                tables = set(inspect(conn).get_table_names())
                conn.commit()  # end the inspect's implicit transaction so Alembic owns its own
                if "projects" in tables and "alembic_version" not in tables:
                    logger.info("existing pre-migration schema found; stamping it at %s", BASELINE)
                    command.stamp(cfg, BASELINE)
                    conn.commit()
                command.upgrade(cfg, "head")
                conn.commit()
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
                conn.commit()
    finally:
        engine.dispose()