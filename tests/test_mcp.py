"""
MCP layer end to end, fully in-process: an MCP client talks to the MCP
server, whose HTTP calls go straight into the FastAPI app via ASGI
transport, which hits the real test database. No ports, no subprocesses.
"""

import asyncio
import json
import subprocess
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


def agent(repo: Path, name: str, heartbeat_seconds: int = 120, join_key: str | None = None, project: str = "mcp-test"):
    settings = MCPSettings(
        coordinator_url="http://ct", project=project, agent=name, role=None, join_key=join_key,
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


# ---------------------------------------------------------------- git reconciliation

def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *args],
                   check=True, capture_output=True)


@pytest.fixture
def git_repo(repo: Path) -> Path:
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "start")
    return repo


def edit_line(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"test edit doesn't match the file: {old!r}"
    path.write_text(text.replace(old, new))


async def test_reconcile_flags_edits_made_without_a_claim(git_repo):
    _, a_client = agent(git_repo, "a")
    async with a_client as a:
        await call(a, "claim", file_path="app/service.py", symbol_name="helper")   # session base recorded here
        svc = git_repo / "app" / "service.py"
        edit_line(svc, "return 42", "return 43")                                  # inside the claim
        edit_line(svc, 'return {"name": name}', 'return {"name": name.strip()}')  # UserService.create: unclaimed
        git(git_repo, "commit", "-qam", "agent work")                             # committed edits count too
        (git_repo / "notes.txt").write_text("new file\n")                         # untracked: listed, not flagged
        r = await call(a, "reconcile")
        assert r["clean"] is False
        [problem] = r["edits_outside_your_claims"]
        assert problem["file_path"] == "app/service.py" and problem["symbols"] == ["UserService.create"]
        assert problem["inside_claims_of"] == []
        assert r["new_files"] == ["notes.txt"]


async def test_release_reconciles_and_reports_edits_inside_another_agents_claim(git_repo):
    _, a_client = agent(git_repo, "a")
    _, b_client = agent(git_repo, "b")
    async with a_client as a, b_client as b:
        await call(b, "claim", file_path="app/service.py", symbol_name="UserService.create", note="validation")
        c = await call(a, "claim", file_path="app/service.py", symbol_name="helper")
        edit_line(git_repo / "app" / "service.py", 'return {"name": name}', "return {}")  # inside b's claim
        r = await call(a, "release", claim_id=c["claim_id"], note="done")
        assert r["released"] and r["reconcile"]["clean"] is False
        assert r["reconcile"]["edits_outside_your_claims"][0]["inside_claims_of"] == ["b (UserService.create)"]
        assert "Message them" in r["reconcile"]["what_to_do"]
        feed = await call(b, "catch_up")
        assert any(e["type"] == "edit_conflict" for e in feed["new_activity"])


async def test_reconcile_outside_a_git_repo_says_why(repo):
    _, a_client = agent(repo, "a")
    async with a_client as a:
        assert "reconciliation is off" in (await call(a, "reconcile"))["error"]
        c = await call(a, "claim", file_path="app/service.py", symbol_name="helper")
        assert "reconcile" not in await call(a, "release", claim_id=c["claim_id"])  # release still works


# ---------------------------------------------------------------- auth

async def test_agents_join_with_a_key_when_auth_is_on(repo, auth_on):
    from fastapi.testclient import TestClient
    project = TestClient(app).post("/projects", json={"name": "locked"}, headers=auth_on).json()
    _, good = agent(repo, "a", join_key=project["join_key"], project="locked")
    _, bad = agent(repo, "b", join_key="wrong", project="locked")
    async with good as a, bad as b:
        assert (await call(a, "claim", file_path="app/service.py", symbol_name="helper"))["granted"] is True
        assert (await call(a, "status"))["project"] == "locked"
        assert "join key" in (await call(b, "status"))["error"]


def test_diff_parser_handles_additions_deletions_and_renames():
    from mcp_server.gitdiff import parse_diff
    diff = """\
diff --git a/app/x.py b/app/x.py
index 1..2 100644
--- a/app/x.py
+++ b/app/x.py
@@ -3,0 +4,2 @@ def f():
@@ -9,3 +11 @@
@@ -20 +19,0 @@
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,4 +0,0 @@
diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,3 @@
diff --git a/a.py b/b.py
similarity index 90%
rename from a.py
rename to b.py
--- a/a.py
+++ b/b.py
@@ -2 +2 @@
diff --git a/img.png b/img.png
Binary files a/img.png and b/img.png differ
"""
    got = {c.path: (c.status, c.ranges) for c in parse_diff(diff)}
    assert got == {
        "app/x.py": ("modified", [(4, 5), (11, 11), (19, 19)]),  # +19,0 is a pure deletion, pinned to line 19
        "old.py": ("deleted", []),
        "new.py": ("added", [(1, 3)]),
        "b.py": ("modified", [(2, 2)]),
    }