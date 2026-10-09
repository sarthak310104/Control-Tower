"""The migrations build exactly the schema the models describe, and upgrade old databases in place."""

import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db import Base
from app.migrate import alembic_config, run_migrations
from conftest import TEST_DB

SCRATCH = "ct_migration_scratch"


@pytest.fixture
def scratch_url():
    """A throwaway database just for these tests (CREATE DATABASE needs autocommit)."""
    admin = create_engine(make_url(TEST_DB).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        c.execute(text(f"CREATE DATABASE {SCRATCH}"))
    url = make_url(TEST_DB).set(database=SCRATCH).render_as_string(hide_password=False)
    yield url
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
    admin.dispose()


def test_migrations_match_the_models(scratch_url):
    run_migrations(scratch_url)
    eng = create_engine(scratch_url)
    with eng.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    eng.dispose()
    assert diffs == [], f"models and migrations disagree; add a migration: {diffs}"


def test_running_migrations_twice_is_a_no_op(scratch_url):
    run_migrations(scratch_url)
    run_migrations(scratch_url)


def test_pre_migration_database_is_stamped_and_upgraded_keeping_data(scratch_url):
    # Build the baseline schema, then drop alembic_version: that's what a
    # database made by create_all() before migrations existed looks like.
    cfg = alembic_config()
    eng = create_engine(scratch_url)
    with eng.connect() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001_baseline")
        conn.commit()
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("INSERT INTO projects (name) VALUES ('kept')"))
        conn.commit()
    run_migrations(scratch_url)
    with eng.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0003_auth"
        assert conn.execute(text("SELECT name FROM projects")).scalars().all() == ["kept"]
    eng.dispose()


def test_downgrade_to_nothing_and_back(scratch_url):
    run_migrations(scratch_url)
    cfg = alembic_config()
    eng = create_engine(scratch_url)
    with eng.connect() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
        conn.commit()
    eng.dispose()
    run_migrations(scratch_url)