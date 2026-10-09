import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"{name} must be an integer, got {raw!r}")


@dataclass(frozen=True)
class MCPSettings:
    """
    Everything one agent's MCP process needs to know. Set per agent in that
    agent's MCP client config (see README), so two agents on the same repo
    differ only in CT_AGENT / CT_ROLE.
    """

    coordinator_url: str = field(default_factory=lambda: os.getenv("CT_URL", "http://localhost:8000").rstrip("/"))
    project: str = field(default_factory=lambda: os.getenv("CT_PROJECT", "default"))
    agent: str = field(default_factory=lambda: os.getenv("CT_AGENT", ""))
    role: str | None = field(default_factory=lambda: os.getenv("CT_ROLE") or None)
    # The agent's checkout. Files are read from here and sent to the
    # coordinator, which itself never touches anyone's disk.
    repo_root: Path = field(default_factory=lambda: Path(os.getenv("CT_REPO_ROOT") or os.getcwd()).resolve())
    # Must stay comfortably below the coordinator's CLAIM_TTL_SECONDS (600 by default).
    heartbeat_seconds: int = field(default_factory=lambda: _env_int("CT_HEARTBEAT_SECONDS", 120))
    request_timeout_seconds: int = field(default_factory=lambda: _env_int("CT_TIMEOUT_SECONDS", 10))

    def validate(self) -> "MCPSettings":
        if not self.agent.strip():
            raise SystemExit("CT_AGENT is required: a stable name for this agent, e.g. 'claude-backend'")
        if not self.repo_root.is_dir():
            raise SystemExit(f"CT_REPO_ROOT does not exist or is not a directory: {self.repo_root}")
        if self.heartbeat_seconds < 5:
            raise SystemExit("CT_HEARTBEAT_SECONDS must be at least 5")
        return self