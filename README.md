# Control Tower

![tests](https://github.com/sarthak310104/Control-Tower/actions/workflows/ci.yml/badge.svg)

A coordination service for multiple AI coding agents working on the same
codebase at once (say Claude Code on the backend and Codex on the frontend).
Agents claim the **specific functions, methods or classes** they are about to
edit, are refused overlapping claims, message each other, and catch up on what
happened while they were away. Agents plug in through an **MCP server**; the
same rules are exposed over a plain **REST API** for anything that doesn't
speak MCP.

```
 Claude Code ──stdio──> MCP server (per agent) ──┐
                         reads files, heartbeats  │ HTTP      ┌─ Postgres
 Codex ────────stdio──> MCP server (per agent) ──┼────────> REST API ─┤
                                                  │           └─ reaper (expires dead claims)
 any script ─────────────────────────────────────┘
```

## Why function-level claims, not file locks

File locking is the obvious first idea and it's wrong here: two agents
editing different functions in the same file would constantly block each
other for no reason. Claims are made on the thing actually being edited, and
overlap is computed from real code structure.

`app/services/ast_conflict.py` is pure Python with no I/O, so the algorithm
is testable on its own:

- It parses the file with the stdlib `ast` module and records every
  function, async function, class and method under a dotted name
  (`UserService.create`, `outer.inner`) with its exact line span.
- **A claim's identity is the symbol name, never its line numbers.** Lines
  drift the moment someone edits code above a claimed function. Every
  conflict check re-parses the *current* file content and re-resolves every
  existing claim to fresh lines before comparing, so stale ranges can't
  cause missed or phantom conflicts. The stored line numbers are a display
  snapshot only.
- Claiming a class conflicts with claiming any method inside it, with no
  special-casing: a class's span contains its methods' spans.
- If a claimed symbol has been renamed or deleted, the stale claim is
  invalidated on the spot and logged, instead of blocking work forever.
- If the file is currently invalid Python (realistically: another agent
  mid-edit) it can't tell "deleted" from "half-written", so it keeps
  existing claims alive using their last known lines. New symbol claims on
  that file get a clear error asking for a line range instead.

Python only for now. Other languages use explicit line-range claims, which
are a first-class path in the API. tree-sitter would be the general answer.

## The MCP server

`mcp_server/` is one small process per agent session, launched by that
agent's harness over stdio. The REST API owns every rule; the MCP layer only
handles what has to live next to the agent:

- **Files.** The agent names a file; the MCP server reads its current
  content from the agent's own checkout and sends it. The coordinator never
  touches anyone's disk, which is what lets agents on different machines
  share one coordinator.
- **Canonical paths.** `app\main.py` (Windows), `./app/main.py` and an
  absolute path all collapse to `app/main.py`. Without this, three agents
  could "claim" the same function under three spellings and all be granted.
  Paths outside the repo root are rejected.
- **Liveness without discipline.** A background task heartbeats every claim
  the agent holds. Claims live exactly as long as the agent's session; when
  the session ends, heartbeats stop and the reaper frees them one TTL later.
  Agents never have to remember to heartbeat.
- **Cursors.** It remembers what the agent has already seen, so `catch_up`
  returns only what's new.
- **Refusals are results, not errors.** A refused claim returns who holds
  the code, their note, and what to do next, so the model acts on it rather
  than retrying blindly.

Tools: `catch_up`, `claim`, `release`, `release_all`, `check` (dry run),
`list_symbols` (a file's symbols and who holds each), `board`,
`send_message`, `agents`, `status`. The server also ships instructions that
tell the model the protocol: catch up first, claim before editing, release
with a note.

### Connecting an agent

Start the coordinator once:

```bash
docker compose up -d                 # Postgres
pip install -r requirements.txt
uvicorn app.main:app
```

Then register the MCP server in each agent's harness, run from the repo the
agent is working on (that folder becomes the default `CT_REPO_ROOT`). For
Claude Code:

```bash
claude mcp add control-tower \
  -e CT_PROJECT=my-app -e CT_AGENT=claude-backend -e CT_ROLE=backend \
  -- python /path/to/Control-Tower/run_mcp.py
```

Or any MCP client that reads a JSON config (`.mcp.json`, Cursor, etc.):

```json
{
  "mcpServers": {
    "control-tower": {
      "command": "python",
      "args": ["/path/to/Control-Tower/run_mcp.py"],
      "env": { "CT_PROJECT": "my-app", "CT_AGENT": "codex-frontend", "CT_ROLE": "frontend" }
    }
  }
}
```

| Variable | Default | Meaning |
|---|---|---|
| `CT_AGENT` | (required) | Stable name for this agent; re-registering with the same name is idempotent |
| `CT_PROJECT` | `default` | Project to join (created on first use) |
| `CT_ROLE` | none | Informational, shown to other agents |
| `CT_URL` | `http://localhost:8000` | Where the coordinator runs |
| `CT_REPO_ROOT` | launch directory | The agent's checkout |
| `CT_HEARTBEAT_SECONDS` | `120` | Keep well under the coordinator's `CLAIM_TTL_SECONDS` (600) |

## Dashboard

Open `http://localhost:8000/ui` once the coordinator is running. Pick a
workspace (one per codebase your agents share), or load the sample project
to see everything working before any real agent connects. The page polls the
API every 2 seconds.

- **Home** is a 3D view of the codebase, rendered through a dithered pixel
  pass. Each file is a wireframe tower and each floor is a line. Active claims
  are glowing slabs in the holder's colour, at the exact lines they cover.
  Hover a floor to see the file, line, function and who holds it. Click a
  free floor to claim that function (or just that line) as any agent, through
  the real API. A refused claim flashes the tower orange.
- **Dashboard** opens with *Orbit*: the repo as a wireframe core, files as
  nodes on its surface, and agents as satellites on their own orbits. A claim
  is a beam of light from an agent to a file. Collisions, new claims and
  messages from the live activity feed play as shockwaves, blooms and pulses.
  Below it, a **timeline** shows one lane per agent over the last 10, 30 or
  60 minutes: hover to scrub back and see what everyone held at that moment.
- **Claims** lists active claims by file, with a countdown to expiry if
  heartbeats stop. Opening one shows the file with the claimed lines
  highlighted.
- **Agents** and **Activity** show each agent's claims, the full event feed,
  and messages between agents.
- **Simulate** (`S`) registers agents, claims, dry-runs and sends messages by
  hand. It makes the same REST calls an agent's MCP server makes.

The coordinator never reads anyone's disk, so to draw files it keeps the
latest content an agent sent with a claim (`GET /files`, `GET /files/content`).
That snapshot can lag behind the real file, and the code view says how old it
is.

The sample project (`POST /demo/seed`) only ever resets the project named by
`DEMO_PROJECT` (default `storefront-demo`). Set `DEMO_ENABLED=false` on a
shared coordinator.

Plain HTML, CSS and JavaScript with no build step; Three.js loads from a CDN
and the page falls back to a flat background without WebGL. Agent-supplied
text (names, notes, messages, code) is always rendered as text, never HTML,
so one agent can't inject markup into the page someone else is watching.

## REST API

Interactive docs at `/docs` once running. The main endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /projects`, `POST /agents` | Idempotent create / register |
| `POST /claims` | Claim a symbol or line range; `409` with the holder on conflict |
| `POST /claims/check` | Same logic, writes nothing |
| `POST /claims/{id}/release` | Owner-only release, with a note |
| `POST /claims/heartbeat?agent_id=` | Refresh all of an agent's claims |
| `GET /claims` | Filter by file, status, agent |
| `POST /messages`, `GET /messages/inbox` | Direct or broadcast; cursor-based inbox |
| `GET /activity` | Append-only feed: registrations, claims, refusals, expiries, messages |
| `GET /messages` | Project-wide message feed (used by the dashboard) |
| `POST /claims/symbols` | Claimable symbols in a piece of source (stateless) |
| `GET /files`, `GET /files/content` | Latest file snapshots agents sent, with symbols |
| `POST /demo/seed` | Load or reset the sample project |

Cursors are message/event ids, not timestamps, so clock skew between
machines can't drop or repeat anything.

## Concurrency

Checking for conflicts and then inserting a claim is a check-then-act race:
two agents claiming the same function at the same instant can both see "no
conflicts" and both be granted. A row lock can't prevent it because the
conflicting row doesn't exist yet. Each claim therefore takes a
transaction-scoped Postgres advisory lock keyed on (project, file), which
serialises claims on the same file while claims on different files never
contend. A test fires 12 simultaneous claims at one function and requires
exactly one `201`; with the lock removed it fails every time.

The reaper uses `SELECT ... FOR UPDATE SKIP LOCKED`, so it never blocks a
claim in progress and two coordinator instances can't expire the same claim
twice.

## Bugs found by actually running it

The first version was written and unit-tested at the algorithm level but
never run end to end. Running it against real Postgres turned up:

- **Every claim was born already expired.** `server_default="now()"` was
  passed as a string, so SQLAlchemy emitted the literal `'now()'`, which
  Postgres evaluates once at `CREATE TABLE`. Every row's
  `last_heartbeat_at` was the table's creation time, so once the database
  was older than the TTL, the reaper killed each new claim within 30
  seconds. Fixed with `func.now()`; a regression test checks defaults are
  per-row.
- **A half-written file wiped everyone's claims.** "Can't parse the file"
  was handled the same as "symbol was deleted", so a single line-range claim
  on a mid-edit file invalidated every symbol claim on it.
- **The concurrent-claim race** described above.
- **Startup crash on some hosts.** APScheduler resolves the machine's local
  timezone and rejects legacy aliases like `Asia/Calcutta`. The scheduler is
  now pinned to UTC.
- Unknown agent or project ids surfaced as 500s from foreign-key errors
  (now clean 404/422), an agent could act inside a project it never joined,
  and agents received their own broadcasts back in their inbox.

## Tests

```bash
pip install -r requirements-dev.txt
createdb agent_coordinator_test      # or set TEST_DATABASE_URL
pytest
```

44 tests across three layers, run in CI against Postgres 16:

- the AST algorithm in isolation (nesting, decorators, drift, overlap edges);
- the API against a real database, including the race, the reaper, and
  regression tests for each bug above;
- the MCP server end to end, in-process: an MCP client calls tools on the
  MCP server, whose HTTP goes straight into the FastAPI app over an ASGI
  transport. Covers two agents coordinating, Windows paths, incremental
  catch-up, the background heartbeat, and the error an agent sees when the
  coordinator is down.

Integration tests use real Postgres rather than SQLite on purpose: advisory
locks, `SKIP LOCKED`, JSONB and server-side defaults are exactly where
SQLite would pass and production would fail.

## Known limitations

- **Claims are advisory.** Nothing stops an agent editing code it never
  claimed; Control Tower relies on agents following the protocol. The
  planned fix is git reconciliation: diff what an agent changed against
  what it claimed and flag the difference.
- **Line-range claims don't move.** Symbol claims track code as it shifts;
  a line-range claim covers the same line numbers even after code above it
  changes. Prefer symbol claims for Python.
- **A forgotten claim lives for the whole session.** Heartbeats tie claims
  to the session, not to the work. `release_all` and the server's
  instructions mitigate this; a maximum claim age would close it.
- **No auth.** Fine on localhost or a trusted network; anything wider needs
  per-agent tokens.
- Tables are created on startup with `create_all`; schema changes after
  this point should move to Alembic migrations.

## Layout

```
app/
  services/ast_conflict.py   pure conflict algorithm
  services/claims.py         claim orchestration, advisory lock, reaper sweep
  services/reaper.py         APScheduler job
  routers/                   REST endpoints
frontend/                    dashboard served at /ui (index.html, styles.css, js/)
mcp_server/
  server.py                  MCP tools, session state, heartbeat loop
  client.py                  async REST client
  workspace.py               path canonicalisation, file reading
run_mcp.py                   launcher for agent harnesses
tests/
```