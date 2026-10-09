# Control Tower

![tests](https://github.com/sarthak310104/Control-Tower/actions/workflows/ci.yml/badge.svg)

**Live demo: [control-tower-lv5k.onrender.com](https://control-tower-lv5k.onrender.com/ui/)** (free hosting, so the first load can take up to a minute while it wakes up). Click "Load the sample project", then "Play a scenario".

A coordination service for multiple AI coding agents working on the same
codebase at once (say Claude Code on the backend and Codex on the frontend).
Agents claim the **specific functions, methods or classes** they are about to
edit, are refused overlapping claims, message each other, and catch up on what
happened while they were away. When an agent finishes, its **git diff is
checked against what it claimed**, so edits made outside a claim are caught
even if the agent skipped the protocol. Agents plug in through an **MCP
server**; the same rules are exposed over a plain **REST API** for anything
that doesn't speak MCP.

```
 Claude Code ──stdio──> MCP server (per agent) ──┐
                         reads files, git diff,   │ HTTP      ┌─ Postgres (Alembic migrations)
                         heartbeats               ├────────> REST API ─┤
 Codex ────────stdio──> MCP server (per agent) ──┤  bearer   └─ reaper (expires dead and stale claims)
                                                  │  tokens
 any script ─────────────────────────────────────┘
```

## Why function-level claims, not file locks

File locking is the obvious first idea and it's wrong here: two agents
editing different functions in the same file would constantly block each
other for no reason. Claims are made on the thing actually being edited, and
overlap is computed from real code structure.

`app/services/ast_conflict.py` is pure Python with no I/O, so the algorithm
is testable on its own:

- It parses the file and records every function, class and method under a
  dotted name (`UserService.create`, `outer.inner`) with its exact line span.
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
- If the file doesn't currently parse (realistically: another agent mid-edit)
  it can't tell "deleted" from "half-written", so it keeps existing claims
  alive using their last known lines. New symbol claims on that file get a
  clear error asking for a line range instead.

### Languages

Python uses the stdlib `ast` module. JavaScript, TypeScript, TSX, Go and Java
use tree-sitter (`app/services/ts_symbols.py`), with the language picked from
the file extension. Every language produces the same dotted names, so the
conflict rules above apply unchanged:

| Language | Extensions | Symbols |
|---|---|---|
| Python | `.py`, `.pyi` | functions, async functions, classes, methods |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` | functions, classes, methods, `const f = () => ...` |
| TypeScript | `.ts`, `.mts`, `.cts`, `.tsx` | the above plus interfaces, enums, type aliases |
| Go | `.go` | functions, types; methods as `Receiver.Method` |
| Java | `.java` | classes, interfaces, enums, records, methods, constructors |

A few rules worth knowing:

- `export` and `export default` are part of the declaration, so claiming
  `Checkout` covers its `export` line too.
- Overloads (TypeScript signatures, Java methods with the same name) share a
  name, so they merge into one span. Claiming `save` claims every overload.
- Go methods hang off their receiver type (`func (s *Store) Get()` is
  `Store.Get`) even when declared far from the type, so claiming `Store`
  doesn't swallow them; that's how Go code is actually split up.
- Any other file type still works through explicit line-range claims.

## Git reconciliation

Claims alone are advisory: nothing stops an agent editing code it never
claimed. Reconciliation closes that loop after the fact.

1. When an agent's MCP server starts, it records the repo's `HEAD`.
2. On `release`, `release_all` or the `reconcile` tool, it runs
   `git diff -U0 <that commit>` locally (plus untracked files) and sends each
   changed file's current content and changed line ranges to
   `POST /reconcile`. Commits made during the session count too.
3. The coordinator re-resolves claims against that content by symbol name,
   exactly like a conflict check, then asks of every changed line: was it
   covered by one of this agent's claims, active or released within
   `RECONCILE_WINDOW_SECONDS` (default 1 hour)?
4. Uncovered lines become an **`edit_unclaimed`** event in the activity feed,
   or **`edit_conflict`** when they fall inside another agent's active claim.
   Each finding names the symbols touched and whose claim was crossed.

The other agent sees it on its next `catch_up`, and the dashboard marks it on
the timeline. Findings are fingerprinted by file and changed text, so
reconciling twice doesn't report the same edit twice. New and deleted files
are listed but not flagged (nobody could have claimed code that didn't
exist), and the coordinator still never reads anyone's disk: the diff runs
next to the agent.

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
- **Git.** It remembers the session's starting commit and produces the diffs
  for reconciliation. Outside a git repo everything else keeps working and
  reconciliation reports that it's unavailable.
- **Liveness without discipline.** A background task heartbeats every claim
  the agent holds. Claims live exactly as long as the agent's session; when
  the session ends, heartbeats stop and the reaper frees them one TTL later.
  Agents never have to remember to heartbeat.
- **Cursors.** It remembers what the agent has already seen, so `catch_up`
  returns only what's new.
- **Refusals are results, not errors.** A refused claim returns who holds
  the code, their note, and what to do next, so the model acts on it rather
  than retrying blindly.

Tools: `catch_up`, `claim`, `release`, `release_all`, `reconcile`, `check`
(dry run), `list_symbols` (a file's symbols and who holds each), `board`,
`send_message`, `agents`, `status`. The server also ships instructions that
tell the model the protocol: catch up first, claim before editing, release
with a note, fix anything reconciliation flags.

### Connecting an agent

Start the coordinator once:

```bash
docker compose up -d                 # Postgres
pip install -r requirements.txt
uvicorn app.main:app                 # applies migrations on startup
```

The easiest way to connect an agent is the dashboard: open a workspace, go
to **Agents** and press **Connect an agent**. It prints the exact command or
JSON for that workspace, join key included. By hand, register the MCP server
in each agent's harness, run from the repo the agent is working on (that
folder becomes the default `CT_REPO_ROOT`). For Claude Code:

```bash
claude mcp add control-tower \
  -e CT_PROJECT=my-app -e CT_AGENT=claude-backend -e CT_ROLE=backend \
  -e CT_JOIN_KEY=ctj_... \
  -- python /path/to/Control-Tower/run_mcp.py
```

Or any MCP client that reads a JSON config (`.mcp.json`, Cursor, etc.):

```json
{
  "mcpServers": {
    "control-tower": {
      "command": "python",
      "args": ["/path/to/Control-Tower/run_mcp.py"],
      "env": {
        "CT_PROJECT": "my-app", "CT_AGENT": "codex-frontend",
        "CT_ROLE": "frontend", "CT_JOIN_KEY": "ctj_..."
      }
    }
  }
}
```

| Variable | Default | Meaning |
|---|---|---|
| `CT_AGENT` | (required) | Stable name for this agent; joining again with the same name is idempotent |
| `CT_PROJECT` | `default` | Project to join (created on first use when auth is off) |
| `CT_JOIN_KEY` | none | The project's join key; required when the coordinator has auth on |
| `CT_ROLE` | none | Informational, shown to other agents |
| `CT_URL` | `http://localhost:8000` | Where the coordinator runs |
| `CT_REPO_ROOT` | launch directory | The agent's checkout |
| `CT_HEARTBEAT_SECONDS` | `120` | Keep well under the coordinator's `CLAIM_TTL_SECONDS` (600) |

## Auth

Off by default, which is fine on localhost: the coordinator logs a warning
and allows every call. Set `CT_ADMIN_TOKEN` to turn it on, and every API call
except `/health` and the dashboard's static files needs
`Authorization: Bearer <token>`.

| Credential | How you get it | What it can do |
|---|---|---|
| Admin token | `CT_ADMIN_TOKEN` | Everything: create projects, rotate join keys, act as any agent, load the sample project |
| Join key (`ctj_...`) | Returned once when a project is created, or by `POST /projects/{id}/join-key` | Only `POST /agents/join` for that one project |
| Agent token (`cta_...`) | Returned by `POST /agents/join` | Read its own project, and claim, release, heartbeat, message and reconcile **only as itself** |

So an agent can't release someone else's claim, post as another agent or
look at other projects, and a leaked join key can be rotated without
touching the admin token. The MCP server does the join for you: it presents
`CT_JOIN_KEY` once and keeps the agent token in memory. `DELETE
/agents/{id}/tokens` revokes an agent's tokens.

Join keys and agent tokens are random, shown once, and stored only as
SHA-256 hashes; the admin token is compared in constant time. The dashboard
asks for the admin token and keeps it in session storage for that tab.

## Operational limits

| Setting | Default | Meaning |
|---|---|---|
| `CLAIM_TTL_SECONDS` | `600` | A claim with no heartbeat for this long expires |
| `MAX_CLAIM_AGE_SECONDS` | `14400` (4 h) | A claim this old expires even if its session keeps heartbeating, so a forgotten claim can't block others all day. `0` disables |
| `RECONCILE_WINDOW_SECONDS` | `3600` | How long after release a claim still covers the agent's edits |
| `MAX_SOURCE_CHARS` | `2000000` | Largest file content accepted in one request |
| `CORS_ORIGINS` | `*` | Comma-separated browser origins allowed to call the API |
| `AUTO_MIGRATE` | `true` | Apply migrations on startup |
| `DEMO_ENABLED` | `true` | Allow `POST /demo/seed` |

Expiry events say why (`no_heartbeat` or `max_age`). Names are restricted to
letters, digits, `.`, `_` and `-`, and notes, messages and paths have length
caps, so one misbehaving agent can't fill the feed.

## Database migrations

The schema is managed by Alembic (`migrations/versions/`). By default the
coordinator applies pending migrations on startup, under a Postgres advisory
lock so several instances starting together don't race. A database created
by an older version (tables but no `alembic_version`) is stamped at the
baseline and upgraded in place, so existing data survives the upgrade. To
manage it yourself, set `AUTO_MIGRATE=false` and run `alembic upgrade head`.

The test suite builds its schema through the migrations, not `create_all`,
and checks that the result matches the SQLAlchemy models exactly, so a model
change without a migration fails CI.

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
  is a beam of light from an agent to a file. Collisions, new claims, edits
  outside a claim and messages from the live activity feed play as
  shockwaves, blooms and pulses. Below it, a **timeline** shows one lane per
  agent over the last 10, 30 or 60 minutes, with reconciliation findings
  marked as hollow orange squares: hover to scrub back and see what everyone
  held at that moment.
- **Claims** lists active claims by file, with a countdown to expiry if
  heartbeats stop. Opening one shows the file with the claimed lines
  highlighted.
- **Agents** shows each agent's claims and has **Connect an agent**, which
  generates the setup command for that workspace and can rotate its join key.
- **Activity** is the full event feed and the messages between agents.
- **Simulate** (`S`) registers agents, claims, dry-runs and sends messages by
  hand. It makes the same REST calls an agent's MCP server makes.

The coordinator never reads anyone's disk, so to draw files it keeps the
latest content an agent sent with a claim or a reconcile (`GET /files`,
`GET /files/content`). That snapshot can lag behind the real file, and the
code view says how old it is.

The sample project (`POST /demo/seed`) only ever resets the project named by
`DEMO_PROJECT` (default `storefront-demo`), and includes a TypeScript file and
a reconciliation finding. Set `DEMO_ENABLED=false` on a shared coordinator.

Plain HTML, CSS and JavaScript with no build step; Three.js loads from a CDN
and the page falls back to a flat background without WebGL. Agent-supplied
text (names, notes, messages, code) is always rendered as text, never HTML,
so one agent can't inject markup into the page someone else is watching.

## REST API

Interactive docs at `/docs` once running. The main endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /projects` | Create a project (admin); returns its join key once |
| `POST /projects/{id}/join-key` | Rotate the join key (admin) |
| `POST /agents/join` | Join with `X-Join-Key`; returns an agent token when auth is on |
| `POST /agents` | Register an agent directly (admin) |
| `DELETE /agents/{id}/tokens` | Revoke an agent's tokens |
| `POST /claims` | Claim a symbol or line range; `409` with the holder on conflict |
| `POST /claims/check` | Same logic, writes nothing |
| `POST /claims/{id}/release` | Owner-only release, with a note |
| `POST /claims/heartbeat?agent_id=` | Refresh all of an agent's claims |
| `GET /claims` | Filter by file, status, agent |
| `POST /reconcile` | Check an agent's changed lines against its claims |
| `POST /messages`, `GET /messages/inbox` | Direct or broadcast; cursor-based inbox |
| `GET /messages` | Project-wide message feed (used by the dashboard) |
| `GET /activity` | Append-only feed: registrations, claims, refusals, expiries, messages, edit findings |
| `POST /claims/symbols` | Claimable symbols in a piece of source, any supported language (stateless) |
| `GET /files`, `GET /files/content` | Latest file snapshots agents sent, with symbols |
| `POST /demo/seed` | Load or reset the sample project (admin) |
| `GET /health` | Status plus whether auth is on |

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
- **`export function` was found twice.** The tree-sitter walker visited the
  `export` wrapper and then the declaration inside it, producing
  `Checkout.Checkout`. Each declaration is now walked exactly once.
- **Adding an enum value failed inside a migration.** Postgres won't run
  `ALTER TYPE ... ADD VALUE` in the same transaction that uses it, and the
  startup migration inherited an open transaction from its own schema
  check. The migration now commits first and adds values in an autocommit
  block.
- Unknown agent or project ids surfaced as 500s from foreign-key errors
  (now clean 404/422), an agent could act inside a project it never joined,
  and agents received their own broadcasts back in their inbox.

## Tests

```bash
pip install -r requirements-dev.txt
createdb agent_coordinator_test      # or set TEST_DATABASE_URL
pytest
```

96 tests across six files, run in CI against Postgres 16:

- `test_ast_conflict.py`: the symbol algorithm in isolation for every
  language (nesting, decorators, exports, overloads, Go receivers, drift,
  overlap edges);
- `test_api.py`: the API against a real database, including the race, the
  reaper, max claim age, input limits, and regression tests for each bug
  above;
- `test_reconcile.py`: range arithmetic, covered and uncovered edits,
  crossing another agent's claim, the release window, deduplication;
- `test_auth.py`: join keys, per-agent tokens, acting as another agent,
  cross-project access, revocation;
- `test_migrations.py`: a fresh database, upgrading a legacy database with
  data in it, downgrade and upgrade again, and migrations matching the
  models;
- `test_mcp.py`: the MCP server end to end, in-process: an MCP client calls
  tools on the MCP server, whose HTTP goes straight into the FastAPI app over
  an ASGI transport. Covers two agents coordinating, Windows paths,
  incremental catch-up, the background heartbeat, joining with a key, real
  git repos being reconciled on release, and the error an agent sees when
  the coordinator is down.

Integration tests use real Postgres rather than SQLite on purpose: advisory
locks, `SKIP LOCKED`, JSONB, enum migrations and server-side defaults are
exactly where SQLite would pass and production would fail.

## Known limitations

- **Reconciliation detects, it doesn't prevent.** An unclaimed edit is
  flagged after it happens, and only for agents running the MCP server
  inside a git checkout. It's a safety net under the claim protocol, not a
  lock on the filesystem.
- **New and deleted files aren't flagged** by reconciliation, only listed.
- **Line-range claims don't move.** Symbol claims track code as it shifts;
  a line-range claim covers the same line numbers even after code above it
  changes. Prefer symbol claims in supported languages.
- **Five languages plus Python.** Others fall back to line ranges. Overloads
  are claimed together rather than individually.
- **Auth is off until you set `CT_ADMIN_TOKEN`.** That's the right default
  on localhost and the wrong one anywhere else. There are no per-user
  accounts for the dashboard, just the admin token.

## Layout

```
app/
  services/ast_conflict.py   pure conflict algorithm, language dispatch
  services/ts_symbols.py     tree-sitter symbol extraction (JS, TS, Go, Java)
  services/claims.py         claim orchestration, advisory lock, reaper sweep
  services/reconcile.py      git diff vs claims
  services/reaper.py         APScheduler job
  auth.py                    admin token, join keys, agent tokens
  migrate.py                 startup migrations under an advisory lock
  routers/                   REST endpoints
migrations/                  Alembic environment and versions
frontend/                    dashboard served at /ui (index.html, styles.css, js/)
mcp_server/
  server.py                  MCP tools, session state, heartbeat loop
  client.py                  async REST client
  gitdiff.py                 git diff parsing for reconciliation
  workspace.py               path canonicalisation, file reading
run_mcp.py                   launcher for agent harnesses
tests/
```