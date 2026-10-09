"""
Control Tower MCP server: one process per agent session, launched by that
agent's harness (Claude Code, Cursor, Codex, ...) over stdio.

It is deliberately thin. The REST API owns every rule (conflict detection,
TTLs, ownership); this layer only adds the things that need to live next
to the agent:

- identity: joins the project and registers the agent lazily on first use,
  so a misconfigured or down coordinator never stops the harness booting;
- the filesystem: reads the file's current content from the agent's own
  checkout and canonicalises paths, so the agent only ever names a file;
- cursors: remembers what the agent has already seen in its inbox and the
  activity feed, so "catch up" means "what's new since I last looked";
- liveness: a background heartbeat for every claim this agent holds. The
  agent never has to remember to heartbeat; claims stay alive exactly as
  long as this process does. When the session ends, heartbeats stop and the
  coordinator's reaper frees the claims after one TTL;
- git: remembers HEAD when the session starts, and `reconcile` (also run
  on every release) sends what actually changed since then, so edits made
  without a claim are caught;
- auth: joins with the project's join key when the coordinator needs one
  and uses the per-agent token it gets back for every later call.

Nothing here writes to stdout except the MCP protocol itself (stdio
transport): logs go to stderr.
"""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.services.ast_conflict import UnparsableFileError, UnsupportedLanguageError, extract_symbols
from mcp_server import gitdiff
from mcp_server.client import CoordinatorClient, CoordinatorError, CoordinatorUnavailable
from mcp_server.config import MCPSettings
from mcp_server.workspace import WorkspaceError, canonical_path, read_source

logger = logging.getLogger("control_tower.mcp")

INSTRUCTIONS = """\
You share this codebase with other AI agents, coordinated through Control Tower.
Protocol:
1. At the start of a task, call `catch_up` to see what others are doing and read new messages.
2. Before editing a function, method or class, `claim` it by symbol name (e.g. "UserService.create").
   Symbol claims work in Python, JavaScript, TypeScript, Go and Java; anywhere else, or for code
   that isn't a symbol, claim a line range.
   Use `list_symbols` to see a file's symbols and who holds them.
3. If a claim is refused, don't edit that code. Work on something else, or `send_message`
   to the holder to coordinate.
4. When you finish a piece of work, `release` its claim with a short note of what changed.
   Before ending your session, `release_all`.
5. Releasing also checks your git changes against your claims. If it reports edits outside
   your claims, mention them to the affected agents; if they were inside someone else's
   claim, message that agent right away. You can run `reconcile` any time to check.
Claims are kept alive automatically while this session runs; you never need to heartbeat.
"""


@dataclass
class Session:
    settings: MCPSettings
    api: CoordinatorClient
    project_id: int | None = None
    agent_id: int | None = None
    inbox_cursor: int = 0
    activity_cursor: int = 0
    agent_names: dict[int, str] = field(default_factory=dict)
    git_base: str | None = None  # HEAD when the session started; reconcile diffs against it
    git_note: str | None = None  # why reconciliation is unavailable, if it is
    _join_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def ensure_joined(self) -> tuple[int, int]:
        if self.agent_id is not None:
            return self.project_id, self.agent_id
        async with self._join_lock:
            if self.agent_id is None:
                s = self.settings
                joined = await self.api.join(s.project, s.agent, s.role, s.join_key)
                self.api.use_token(joined["token"])
                agent = joined["agent"]
                self.project_id, self.agent_id = joined["project_id"], agent["id"]
                self.agent_names[agent["id"]] = agent["name"]
                try:
                    self.git_base = await asyncio.to_thread(gitdiff.head, s.repo_root)
                except gitdiff.GitUnavailable as exc:
                    self.git_note = f"reconciliation is off: {exc}"
                logger.info("joined project %r as %r (agent #%s), git base %s",
                            s.project, s.agent, agent["id"], self.git_base or "none")
        return self.project_id, self.agent_id

    async def refresh_agents(self) -> list[dict]:
        project_id, _ = await self.ensure_joined()
        agents = await self.api.list_agents(project_id)
        self.agent_names.update({a["id"]: a["name"] for a in agents})
        return agents

    async def name_of(self, agent_id: int | None) -> str | None:
        if agent_id is None:
            return None
        if agent_id not in self.agent_names:
            await self.refresh_agents()
        return self.agent_names.get(agent_id, f"agent#{agent_id}")

    async def id_of(self, agent_name: str) -> int:
        for known_id, known in self.agent_names.items():
            if known == agent_name:
                return known_id
        agents = await self.refresh_agents()
        for a in agents:
            if a["name"] == agent_name:
                return a["id"]
        names = ", ".join(sorted(a["name"] for a in agents)) or "none"
        raise ToolError(f"no agent named {agent_name!r} in this project (agents: {names})")


def _describe_claim(c: dict, holder: str | None = None) -> dict:
    out = {
        "claim_id": c["id"],
        "file_path": c["file_path"],
        "target": c["symbol_name"] or f"lines {c['line_start']}-{c['line_end']}",
        "lines_at_claim_time": [c["line_start"], c["line_end"]],
        "note": c["note"],
    }
    if holder is not None:
        out["held_by"] = holder
    return out


def _claim_body(project_id: int, agent_id: int, rel: str, source: str,
                symbol_name: str | None, line_start: int | None, line_end: int | None,
                note: str | None) -> dict:
    if symbol_name and (line_start is not None or line_end is not None):
        raise ToolError("pass either symbol_name or line_start/line_end, not both")
    if not symbol_name:
        if line_start is None or line_end is None:
            raise ToolError("pass symbol_name, or both line_start and line_end")
        if line_start < 1 or line_end < line_start:
            raise ToolError("line range must satisfy 1 <= line_start <= line_end")
    return {
        "project_id": project_id, "agent_id": agent_id, "file_path": rel, "source": source,
        "symbol_name": symbol_name or None, "line_start": line_start, "line_end": line_end, "note": note,
    }


async def _heartbeat_loop(session: Session) -> None:
    while True:
        await asyncio.sleep(session.settings.heartbeat_seconds)
        if session.agent_id is None:
            continue
        try:
            result = await session.api.heartbeat_all(session.agent_id)
            if result.get("claims_refreshed"):
                logger.debug("heartbeat refreshed %s claim(s)", result["claims_refreshed"])
        except Exception as exc:  # never let a blip kill the loop; next tick retries
            logger.warning("heartbeat failed: %s", exc)


def build_server(settings: MCPSettings, transport: httpx.AsyncBaseTransport | None = None) -> MCPServer:
    api = CoordinatorClient(settings.coordinator_url, settings.request_timeout_seconds, transport)
    session = Session(settings, api)

    @asynccontextmanager
    async def lifespan(_server):
        task = asyncio.create_task(_heartbeat_loop(session))
        try:
            yield {"session": session}
        finally:
            task.cancel()
            await api.aclose()

    mcp = MCPServer(
        name="control-tower",
        title="Control Tower",
        instructions=INSTRUCTIONS,
        version="0.3.0",
        lifespan=lifespan,
    )

    def _translate(exc: Exception) -> ToolError:
        if isinstance(exc, ToolError):
            return exc
        if isinstance(exc, (CoordinatorUnavailable, WorkspaceError)):
            return ToolError(str(exc))
        if isinstance(exc, CoordinatorError):
            detail = exc.detail.get("message", exc.detail) if isinstance(exc.detail, dict) else exc.detail
            return ToolError(str(detail))
        return ToolError(f"{exc.__class__.__name__}: {exc}")

    def tool(fn):
        """Registers fn as a tool, converting expected failures into clean tool errors for the model."""
        import functools

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                raise _translate(exc) from exc

        return mcp.tool()(wrapper)

    @tool
    async def status() -> dict:
        """Who you are in Control Tower, the claims you currently hold, and the other agents in the project."""
        project_id, agent_id = await session.ensure_joined()
        mine = await api.list_claims(project_id, agent_id=agent_id, status="active")
        agents = await session.refresh_agents()
        return {
            "project": settings.project,
            "agent": settings.agent,
            "role": settings.role,
            "repo_root": str(settings.repo_root),
            "my_active_claims": [_describe_claim(c) for c in mine],
            "other_agents": [
                {"name": a["name"], "role": a["role"], "last_seen_at": a["last_seen_at"]}
                for a in agents if a["id"] != agent_id
            ],
        }

    @tool
    async def catch_up(limit: int = 50) -> dict:
        """
        What happened since you last looked: new activity (claims taken/released/expired, messages)
        and unread messages addressed to you or broadcast. Call this at the start of every task.
        """
        project_id, agent_id = await session.ensure_joined()
        events = await api.activity(project_id, session.activity_cursor, max(1, min(limit, 200)))
        messages = await api.inbox(project_id, agent_id, session.inbox_cursor)
        if events:
            session.activity_cursor = events[-1]["id"]
        if messages:
            session.inbox_cursor = messages[-1]["id"]
        active = await api.list_claims(project_id, status="active")
        return {
            "new_activity": [
                {"type": e["type"], "agent": await session.name_of(e["agent_id"]), "at": e["created_at"], **e["payload"]}
                for e in events if e["agent_id"] != agent_id
            ],
            "unread_messages": [
                {"from": await session.name_of(m["from_agent_id"]),
                 "to": "you" if m["to_agent_id"] == agent_id else "everyone",
                 "body": m["body"], "at": m["created_at"]}
                for m in messages
            ],
            "claims_held_by_others": [
                _describe_claim(c, await session.name_of(c["agent_id"])) for c in active if c["agent_id"] != agent_id
            ],
            "more_activity_pending": len(events) == max(1, min(limit, 200)),
        }

    @tool
    async def list_symbols(file_path: str) -> dict:
        """
        The functions, classes and methods in a file (dotted names, current line ranges), and which
        of them are currently claimed and by whom. Works for Python, JavaScript, TypeScript, Go and
        Java. Use the names here with `claim`.
        """
        project_id, agent_id = await session.ensure_joined()
        rel = canonical_path(settings.repo_root, file_path)
        source = read_source(settings.repo_root, rel)
        try:
            symbols = extract_symbols(source, rel)
        except UnsupportedLanguageError as exc:
            raise ToolError(str(exc))
        except UnparsableFileError as exc:
            raise ToolError(f"{rel} doesn't parse right now ({exc}); claim a line range instead")
        active = await api.list_claims(project_id, file_path=rel, status="active")
        by_symbol = {c["symbol_name"]: c for c in active if c["symbol_name"]}
        line_claims = [c for c in active if not c["symbol_name"]]
        rows = []
        for name, rng in symbols.items():
            row = {"symbol": name, "lines": [rng.line_start, rng.line_end]}
            holder = by_symbol.get(name)
            if holder:
                row["claimed_by"] = "you" if holder["agent_id"] == agent_id else await session.name_of(holder["agent_id"])
                row["claim_id"] = holder["id"]
            rows.append(row)
        return {
            "file_path": rel,
            "symbols": rows,
            "line_range_claims": [
                _describe_claim(c, "you" if c["agent_id"] == agent_id else await session.name_of(c["agent_id"]))
                for c in line_claims
            ],
            "note": "a claim on a class also covers every method inside it",
        }

    @tool
    async def check(
        file_path: str,
        symbol_name: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> dict:
        """Dry run of `claim`: would this collide with another agent's work, and whose? Changes nothing."""
        project_id, agent_id = await session.ensure_joined()
        rel = canonical_path(settings.repo_root, file_path)
        source = read_source(settings.repo_root, rel)
        body = _claim_body(project_id, agent_id, rel, source, symbol_name, line_start, line_end, None)
        result = await api.check(body)
        return {
            "file_path": rel,
            "target": symbol_name or f"lines {line_start}-{line_end}",
            "current_lines": [result["line_start"], result["line_end"]],
            "free": result["ok"],
            "conflicts": result["conflicts"],
        }

    @tool
    async def claim(
        file_path: str,
        symbol_name: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        note: str | None = None,
    ) -> dict:
        """
        Claim a function/method/class (by dotted symbol name; Python, JS/TS, Go, Java) or a line range (any file)
        before editing it. Other agents are then refused overlapping claims until you release.
        `note` should say what you're about to do; other agents see it.
        """
        project_id, agent_id = await session.ensure_joined()
        rel = canonical_path(settings.repo_root, file_path)
        source = read_source(settings.repo_root, rel)
        body = _claim_body(project_id, agent_id, rel, source, symbol_name, line_start, line_end, note)
        try:
            c = await api.claim(body)
        except CoordinatorError as exc:
            if exc.status != 409 or not isinstance(exc.detail, dict):
                raise
            d = exc.detail
            # A refusal is an expected outcome the agent should act on, not a tool failure.
            return {
                "granted": False,
                "file_path": rel,
                "target": symbol_name or f"lines {line_start}-{line_end}",
                "held_by": d.get("conflicting_agent"),
                "their_claim_id": d.get("conflicting_claim_id"),
                "their_target": d.get("symbol_name") or f"lines {d.get('line_start')}-{d.get('line_end')}",
                "their_note": d.get("note"),
                "what_to_do": (
                    "Do not edit this code. Pick other work, or send_message to "
                    f"{d.get('conflicting_agent')!r} to coordinate."
                ),
            }
        return {"granted": True, **_describe_claim(c)}

    @tool
    async def release(claim_id: int, note: str | None = None) -> dict:
        """
        Release one of your claims when you're done. `note` should summarise what you changed.
        Also checks your git changes in that file against your claims.
        """
        project_id, agent_id = await session.ensure_joined()
        c = await api.release(claim_id, agent_id, note)
        out = {"released": True, **_describe_claim(c)}
        check = await run_reconcile(project_id, agent_id, only={c["file_path"]})
        if check:
            out["reconcile"] = check
        return out

    @tool
    async def release_all(note: str | None = None) -> dict:
        """Release every claim you hold, then check all your git changes against your claims. Call before your session ends."""
        project_id, agent_id = await session.ensure_joined()
        mine = await api.list_claims(project_id, agent_id=agent_id, status="active")
        released, failed = [], []
        for c in mine:
            try:
                await api.release(c["id"], agent_id, note)
                released.append(c["id"])
            except CoordinatorError as exc:  # e.g. expired between list and release
                failed.append({"claim_id": c["id"], "reason": str(exc.detail)})
        out = {"released": released, "failed": failed}
        check = await run_reconcile(project_id, agent_id)
        if check:
            out["reconcile"] = check
        return out

    async def run_reconcile(project_id: int, agent_id: int, only: set[str] | None = None) -> dict | None:
        """Sends this session's git changes to POST /reconcile. None when git isn't usable here."""
        if session.git_note:
            return None
        try:
            changes = await asyncio.to_thread(gitdiff.collect, settings.repo_root, session.git_base, only)
        except gitdiff.GitUnavailable as exc:
            session.git_note = f"reconciliation is off: {exc}"
            return None
        files = []
        for ch in changes:
            entry = {"file_path": ch.path, "status": ch.status, "changed": ch.ranges, "source": None}
            if ch.status != "deleted":
                try:
                    entry["source"] = read_source(settings.repo_root, ch.path)
                except WorkspaceError:
                    continue  # binary, too big, or vanished: nothing to compare
                if ch.status == "added":
                    entry["changed"] = []
            files.append(entry)
        if not files:
            return {"clean": True, "summary": "no changes since the session started" if only is None else "no changes in this file"}
        r = await api.reconcile(project_id, agent_id, session.git_base, files)
        problems = [
            {
                "file_path": f["file_path"],
                "lines": [f"{a}-{b}" if a != b else str(a) for a, b in f["ranges"]],
                "symbols": f["symbols"],
                "inside_claims_of": [f"{o['agent']} ({o['target']})" for o in f["overlaps"]],
            }
            for f in r["findings"]
        ]
        out = {
            "clean": r["clean"],
            "summary": (f"all {r['lines_changed']} changed lines were inside your claims" if r["clean"]
                        else f"{r['lines_changed'] - r['lines_covered']} of {r['lines_changed']} changed lines were outside your claims"),
        }
        if problems:
            out["edits_outside_your_claims"] = problems
            if any(p["inside_claims_of"] for p in problems):
                out["what_to_do"] = ("Some of these edits are inside code another agent holds. Message them now "
                                     "(send_message) so they know before they edit it too.")
            else:
                out["what_to_do"] = "These were logged for the other agents. Next time, claim before editing."
        if r["added"]:
            out["new_files"] = r["added"]
        return out

    @tool
    async def reconcile() -> dict:
        """
        Check everything you've changed in git since this session started (commits and uncommitted
        edits) against your claims. Edits outside your claims are reported to the other agents.
        """
        project_id, agent_id = await session.ensure_joined()
        result = await run_reconcile(project_id, agent_id)
        if result is None:
            raise ToolError(session.git_note or "reconciliation isn't available")
        return result

    @tool
    async def board(file_path: str | None = None) -> dict:
        """Every active claim in the project (optionally one file), grouped by file."""
        project_id, agent_id = await session.ensure_joined()
        rel = canonical_path(settings.repo_root, file_path) if file_path else None
        active = await api.list_claims(project_id, file_path=rel, status="active")
        grouped: dict[str, list] = {}
        for c in sorted(active, key=lambda c: (c["file_path"], c["line_start"])):
            holder = "you" if c["agent_id"] == agent_id else await session.name_of(c["agent_id"])
            grouped.setdefault(c["file_path"], []).append(_describe_claim(c, holder))
        return {"files": grouped, "total_active_claims": len(active)}

    @tool
    async def send_message(body: str, to_agent: str | None = None) -> dict:
        """Message one agent by name, or everyone in the project if to_agent is omitted."""
        project_id, agent_id = await session.ensure_joined()
        to_id = await session.id_of(to_agent) if to_agent else None
        if to_id == agent_id:
            raise ToolError("that's you; pick another agent or omit to_agent to broadcast")
        m = await api.send_message(project_id, agent_id, to_id, body)
        return {"sent": True, "message_id": m["id"], "to": to_agent or "everyone"}

    @tool
    async def agents() -> list[dict]:
        """The agents registered in this project and when each was last active."""
        _, agent_id = await session.ensure_joined()
        rows = await session.refresh_agents()
        return [
            {"name": a["name"], "role": a["role"], "last_seen_at": a["last_seen_at"], "is_you": a["id"] == agent_id}
            for a in rows
        ]

    mcp.session_state = session  # exposed for tests
    return mcp


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="[control-tower] %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request drowns the useful logs
    settings = MCPSettings().validate()
    build_server(settings).run("stdio")


if __name__ == "__main__":
    main()