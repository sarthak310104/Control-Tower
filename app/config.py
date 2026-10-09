import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/agent_coordinator"
    )

    # A claim with no heartbeat for longer than this is considered stale and
    # auto-released by the reaper -- protects against an agent crashing or a
    # session just ending mid-task without releasing what it held.
    claim_ttl_seconds: int = int(os.getenv("CLAIM_TTL_SECONDS", "600"))

    # How often the reaper sweeps for expired claims.
    reaper_interval_seconds: int = int(os.getenv("REAPER_INTERVAL_SECONDS", "30"))

    # Base URL the MCP server (a separate process, possibly on a different
    # machine per agent) uses to reach this REST API.
    coordinator_url: str = os.getenv("COORDINATOR_URL", "http://localhost:8000")

    # Lets the dashboard load a sample project (POST /demo/seed). It only ever
    # touches the project named below, but turn it off on a shared coordinator.
    demo_enabled: bool = os.getenv("DEMO_ENABLED", "true").lower() in ("1", "true", "yes")
    demo_project: str = os.getenv("DEMO_PROJECT", "storefront-demo")

    # Auth. With CT_ADMIN_TOKEN unset, the coordinator is open (fine on
    # localhost). Set it to require tokens on every call: the admin token for
    # the dashboard and project management, per-agent tokens for agents (see
    # app/auth.py).
    admin_token: str | None = os.getenv("CT_ADMIN_TOKEN") or None

    # A claim older than this is expired even if its session keeps
    # heartbeating, so a claim an agent forgot about can't block others all
    # day. 0 turns the limit off.
    max_claim_age_seconds: int = int(os.getenv("MAX_CLAIM_AGE_SECONDS", str(4 * 3600)))

    # Reconciliation counts an edit as covered by a claim the agent held now
    # or released within this window (edits usually land just before a release).
    reconcile_window_seconds: int = int(os.getenv("RECONCILE_WINDOW_SECONDS", "3600"))

    # Largest file content accepted in one request, in characters.
    max_source_chars: int = int(os.getenv("MAX_SOURCE_CHARS", "2000000"))

    # Comma-separated origins allowed to call the API from a browser.
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")

    # Apply database migrations on startup. Turn off to run `alembic upgrade head` yourself.
    auto_migrate: bool = os.getenv("AUTO_MIGRATE", "true").lower() in ("1", "true", "yes")

    @property
    def auth_enabled(self) -> bool:
        return bool(self.admin_token)


settings = Settings()