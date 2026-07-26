# Agent Coordinator

A shared background service that lets multiple AI coding agents (e.g. Claude
on backend, GPT on frontend, Copilot somewhere else) working on the same
codebase avoid overwriting each other's work, see what the others are doing,
and pass messages -- without needing a human to manually coordinate them.

## The mental model: a blackboard, not a chat room

AI agents aren't independent listeners waiting for messages -- each only
acts when its own harness invokes it (Claude Code responds to its terminal,
Copilot responds to its own prompts). So "communication" here doesn't mean
real-time push between processes; it means a shared, actively-**polled**
coordination board, in the classic AI "blackboard system" sense: multiple
independent reasoners read and write to common state, and the state itself
is what carries context between them. Each agent's own tool-use loop is
expected to call `claim` before editing, `release` when done, and check
`get_activity` / `get_inbox` periodically to catch up.

## Architecture

```
Claude session --\                                      /-- writes claims, sends messages
                   >-- MCP server (thin, per-agent) --> REST API (Postgres) --< reads activity feed
GPT session ------/                                      \-- reaper sweeps stale claims
```

- **REST API** (`app/`) is the source of truth. Runs centrally -- one
  machine for now, reachable over the network later for the multi-machine
  case, with no code changes needed on this side.
- **MCP server** (`mcp_server/`, not yet built -- see "What's next") is a
  thin per-agent adapter translating MCP tool calls into HTTP calls against
  the REST API. Kept separate from the REST core on purpose: agents/tools
  that don't speak MCP can hit the REST API directly, and the REST API is
  independently testable without any MCP machinery involved.
- **Reaper** (`app/services/reaper.py`) is the actual background worker:
  an in-process APScheduler job that sweeps claims with no heartbeat within
  the TTL and marks them expired, so a crashed agent or an ended session
  doesn't hold a lock forever.

## The hard problem: AST-level claims, not file-level

File-level locking is the obvious first idea and it's wrong for this: two
agents editing *different functions in the same file* would constantly and
falsely block each other. The claim needs to be at the granularity of the
actual thing being edited -- a function, method, or class -- with real
overlap detection between them.

`app/services/ast_conflict.py` is deliberately **pure Python with zero DB
dependency** -- no I/O beyond the source string it's handed -- so the
algorithm is independently testable, and it's what I actually tested first,
before wiring up Postgres or any of the routers:

- Parses a file with the `ast` stdlib module, walks every `FunctionDef` /
  `AsyncFunctionDef` / `ClassDef`, and records a dotted qualified name
  (`ClassA.method_b`, `outer_func.inner_closure`) with its exact line span
  (`end_lineno`, available since Python 3.8).
- **Claims are identified by symbol name, never by cached line numbers.**
  This is the actual design insight: line numbers drift the moment code
  above a claimed function changes. Every conflict check re-parses the
  *current* file content and re-resolves every existing active claim's
  symbol to a fresh range before comparing -- so staleness is structurally
  impossible, not just handled with a TTL band-aid. Verified this exact
  property directly: claimed a symbol, inserted new code above it, re-
  resolved against the new content, confirmed the range shifted correctly
  and two different resolutions of the same symbol are never silently
  compared against each other using stale numbers.
- A nice property that falls out for free, not a special case: claiming a
  whole class naturally conflicts with someone else claiming any method
  inside it, because a class's AST span *contains* its methods' spans.
  Confirmed this holds via the same overlap check used for everything else.
- If a claimed symbol has since been renamed or deleted by the time another
  agent checks for conflicts against that file, the stale claim is
  auto-invalidated on the spot (not just left dangling) and logged to the
  activity feed.
- If the file is currently invalid Python -- realistically, another agent
  mid-edit -- symbol resolution can't happen at all. Callers get a clear
  error and are expected to fall back to an explicit line-range claim
  instead of a symbol name until the file is valid again.

**Scoped to Python only for now.** Other languages would need a different
parser per language -- tree-sitter is the obvious general answer for a
polyglot version, not attempted here. Non-Python files must use explicit
line-range claims (`line_start`/`line_end` instead of `symbol_name`); the
API supports this as a first-class path, not an afterthought.

## Why the client sends file content, not a path

The coordinator never reads from the agent's local disk -- every claim
request includes the *current* file content as a string. This is
deliberate, not incidental: it's what makes the "multi-machine later"
requirement work with zero later refactoring, since the coordinator can't
assume filesystem access to wherever an agent's checkout happens to live.

## Data model

- `Project` -- scopes everything below so unrelated work never cross-talks.
- `Agent` -- one participant (e.g. `"claude-backend"`); re-registers
  idempotently each session, `last_seen_at` updates on every call.
- `Claim` -- an agent's declared intent on one symbol or line range in one
  file. `line_start`/`line_end` stored on the row are a **snapshot for
  display only** -- see the docstring in `models.py` and the design note
  above; conflict checks never trust them.
- `Message` -- point-to-point (`to_agent_id` set) or broadcast (null).
- `ActivityEvent` -- append-only feed of everything (registrations, claims,
  releases, expirations, invalidations, messages). What a resuming or
  newly-joining agent reads first to reconstruct context it doesn't have.

## Running it

```bash
docker compose up -d
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The reaper starts automatically with the app (no separate worker process
needed, unlike the Celery setup in a companion project -- a periodic sweep
is light enough not to need a full job queue).

```bash
curl -X POST localhost:8000/projects -d '{"name": "demo"}' -H 'Content-Type: application/json'
curl -X POST localhost:8000/agents -d '{"project_id": 1, "name": "claude-backend", "role": "backend"}' -H 'Content-Type: application/json'
curl -X POST localhost:8000/agents -d '{"project_id": 1, "name": "gpt-frontend", "role": "frontend"}' -H 'Content-Type: application/json'

# claude-backend claims a function
curl -X POST localhost:8000/claims -H 'Content-Type: application/json' -d '{
  "project_id": 1, "agent_id": 1, "file_path": "app/main.py",
  "source": "def foo():\n    pass\n\ndef bar():\n    pass\n",
  "symbol_name": "foo", "note": "adding validation"
}'

# gpt-frontend tries to claim the SAME function -> 409 conflict
curl -X POST localhost:8000/claims -H 'Content-Type: application/json' -d '{
  "project_id": 1, "agent_id": 2, "file_path": "app/main.py",
  "source": "def foo():\n    pass\n\ndef bar():\n    pass\n",
  "symbol_name": "foo"
}'

# gpt-frontend claims a DIFFERENT function in the same file -> succeeds
curl -X POST localhost:8000/claims -H 'Content-Type: application/json' -d '{
  "project_id": 1, "agent_id": 2, "file_path": "app/main.py",
  "source": "def foo():\n    pass\n\ndef bar():\n    pass\n",
  "symbol_name": "bar"
}'

curl "localhost:8000/activity?project_id=1"
```

## What's next (not built yet)

- **MCP server** (`mcp_server/`) -- the primary intended delivery mechanism
  per the project scoping, not yet implemented. Will expose `claim`,
  `release`, `check_conflicts`, `send_message`, `get_inbox`,
  `get_activity_feed` as MCP tools, each a thin `httpx` call into the REST
  API above.
- **Git reconciliation** -- cross-check what an agent actually changed
  (via `git diff`) against what it claimed, to catch claims that were made
  but not honored, or edits made without a claim at all. Natural reuse of
  parsing patterns from the git-history-analytics-engine project.
- Heartbeat is currently manual (an agent calls `/claims/{id}/heartbeat`);
  worth having the MCP layer send this automatically on a timer once that
  layer exists, so agents don't need to remember to do it themselves.
