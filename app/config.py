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


settings = Settings()
