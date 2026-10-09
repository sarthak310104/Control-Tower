"""
MCP layer end to end, fully in-process: an MCP client talks to the MCP
server, whose HTTP calls go straight into the FastAPI app via ASGI
transport, which hits the real test database. No ports, no subprocesses.
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from mcp import Client

from app.main import app
from conftest import SOURCE
from mcp_server.config import MCPSettings
from mcp_server.server import build_server
from mcp_server.workspace import WorkspaceError, canonical_path

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "service.py").write_text(SOURCE)
    (tmp_path / "README.md").write_text("line\n" * 10)
    return tmp_path


def agent(repo: Path, name: str, heartbeat_seconds: int = 120):
    settings = MCPSettings(
        coordinator_url="http://ct", project="mcp-test", agent=name, role=None,
        repo_root=repo.resolve(), heartbeat_seconds=heartbeat_seconds,
    )
    server = build_server(settings, transport=httpx.ASGITransport(app=app))
    return server, Client(server)


async def call(client: Client, tool: str, **args):
    r = await client.call_tool(tool, args)
    text = r.content[0].text
    if r.is_error:
        return {"error": text}
    return json.loads(text)


async def test_two_agents_coordinate(repo):
    _, a_client = agent(repo, "claude-backend")
    _, b_client = agent(repo, "gpt-frontend")
    async with a_client as a, b_client as b:
        got = await call(a, "claim", file_path="app/service.py", symbol_name="UserService.create", note="validation")
        assert got["granted"] is True

        refused = await call(b, "claim", file_path="app\\service.py", symbol_name="UserService")
        assert refused["granted"] is False
        assert refused["held_by"] == "claude-backend" and refused["their_note"] == "validation"

        assert (await call(b, "claim", file_path="./app/service.py", symbol_name="helper"))["granted"] is True

        symbols = {s["symbol"]: s for s in (await call(b, "list_symbols", file_path="app/service.py"))["symbols"]}
        assert symbols["UserService.create"]["claimed_by"] == "claude-backend"
        assert symbols["helper"]["claimed_by"] == "you"
        assert "claimed_by" not in symbols["UserService.delete"]

        board = await call(a, "board")
        assert board["total_active_claims"] == 2 and list(board["files"]) == ["app/service.py"]


async def test_catch_up_is_incremental(repo):
    _, a_client = agent(repo, "a")
    _, b_client = agent(repo, "b")
    async with a_client as a, b_client as b:
        await call(a, "status")
        await call(b, "send_message", body="taking the README", to_agent="a")
        await call(b, "claim", file_path="README.md", line_start=1, line_end=5)

        first = await call(a, "catch_up")
        assert [m["body"] for m in first["unread_messages"]] == ["taking the README"]
        assert {e["type"] for e in first["new_activity"]} >= {"message_sent", "claim_created"}
        assert first["claims_held_by_others"][0]["held_by"] == "b"

        second = await call(a, "catch_up")
        assert second["unread_messages"] == [] and second["new_activity"] == []


async def test_release_and_release_all(repo):
    _, a_client = agent(repo, "a")
    async with a_client as a:
        c1 = await call(a, "claim", file_path="app/service.py", symbol_name="helper")
        await call(a, "claim", file_path="README.md", line_start=1, line_end=2)
        assert (await call(a, "release", claim_id=c1["claim_id"], note="done"))["released"] is True
        assert len((await call(a, "release_all"))["released"]) == 1
        assert (await call(a, "status"))["my_active_claims"] == []


async def test_check_is_a_dry_run(repo):
    _, a_client = agent(repo, "a")
    _, b_client = agent(repo, "b")
    async with a_client as a, b_client as b:
        await call(a, "claim", file_path="app/service.py", symbol_name="helper")
        result = await call(b, "check", file_path="app/service.py", symbol_name="helper")
        assert result["free"] is False and result["conflicts"][0]["agent_name"] == "a"
        assert (await call(b, "status"))["my_active_claims"] == []


async def test_errors_are_readable(repo):
    _, a_client = agent(repo, "a")
    async with a_client as a:
        assert "outside the repo root" in (await call(a, "claim", file_path="../x.py", line_start=1, line_end=1))["error"]
        assert "does not exist" in (await call(a, "claim", file_path="nope.py", symbol_name="f"))["error"]
        assert "not found" in (await call(a, "claim", file_path="app/service.py", symbol_name="ghost"))["error"]
        assert "not both" in (await call(a, "claim", file_path="app/service.py", symbol_name="helper", line_start=1, line_end=2))["error"]
        assert "no agent named" in (await call(a, "send_message", body="hi", to_agent="nobody"))["error"]


async def test_coordinator_down_gives_actionable_error(repo):
    settings = MCPSettings(coordinator_url="http://127.0.0.1:9", project="p", agent="a", repo_root=repo.resolve())
    async with Client(build_server(settings)) as a:
        assert "can't reach the coordinator" in (await call(a, "status"))["error"]


async def test_background_heartbeat_keeps_claims_alive(repo, db):
    from datetime import datetime, timedelta, timezone

    from app.models import Claim

    server, a_client = agent(repo, "a", heartbeat_seconds=1)
    async with a_client as a:
        await call(a, "claim", file_path="app/service.py", symbol_name="helper")
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        db.query(Claim).update({Claim.last_heartbeat_at: old})
        db.commit()
        await asyncio.sleep(2.5)
        db.expire_all()
        assert db.query(Claim).one().last_heartbeat_at > old + timedelta(minutes=59)


@pytest.mark.parametrize("given,expected", [
    ("app/service.py", "app/service.py"),
    ("app\\service.py", "app/service.py"),
    ("./app/../app/service.py", "app/service.py"),
])
def test_canonical_paths(repo, given, expected):
    assert canonical_path(repo.resolve(), given) == expected


def test_canonical_path_accepts_absolute_inside_and_rejects_outside(repo):
    root = repo.resolve()
    assert canonical_path(root, str(root / "app" / "service.py")) == "app/service.py"
    for bad in ("../secret", "/etc/passwd", ".", ""):
        with pytest.raises(WorkspaceError):
            canonical_path(root, bad)