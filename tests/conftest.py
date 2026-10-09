"""
Integration tests run against a real Postgres, not SQLite: the things most
worth testing here (advisory locks, JSONB, server-side timestamp defaults,
SKIP LOCKED) are Postgres behaviour, and SQLite would pass where prod fails.

The schema is built by running the real Alembic migrations, not
create_all(), so every test run also exercises the migrations.

Point TEST_DATABASE_URL at a throwaway database. Every table is truncated
before each test.
"""

import os

TEST_DB = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/agent_coordinator_test"
)
os.environ["DATABASE_URL"] = TEST_DB  # must be set before app.* is imported
os.environ.pop("CT_ADMIN_TOKEN", None)  # tests start with auth off; the auth tests turn it on

import dataclasses

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import config
from app.db import Base, SessionLocal, engine
from app.main import app
from app.migrate import run_migrations


def reset_schema(eng=engine):
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture(scope="session", autouse=True)
def _schema():
    reset_schema()
    run_migrations(TEST_DB)
    yield
    reset_schema()


@pytest.fixture(autouse=True)
def _clean():
    with engine.begin() as conn:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def auth_on(monkeypatch):
    """Turns auth on for one test, with admin token 'admin-secret'."""
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, admin_token="admin-secret"))
    return {"Authorization": "Bearer admin-secret"}


@pytest.fixture
def client():
    # No `with` block: skips the lifespan, so the background reaper doesn't
    # run during tests. Tests drive the reaper function directly instead.
    return TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def project(client):
    return client.post("/projects", json={"name": "test"}).json()


@pytest.fixture
def agents(client, project):
    def make(name):
        return client.post("/agents", json={"project_id": project["id"], "name": name}).json()

    return make("alice"), make("bob")


SOURCE = '''\
class UserService:
    def create(self, name):
        return {"name": name}

    def delete(self, user_id):
        return True


def helper():
    return 42
'''


def claim_body(project, agent, symbol=None, source=SOURCE, file_path="app/service.py", lines=None, note=None):
    body = {"project_id": project["id"], "agent_id": agent["id"], "file_path": file_path,
            "source": source, "symbol_name": symbol, "note": note}
    if lines:
        body["line_start"], body["line_end"] = lines
    return body