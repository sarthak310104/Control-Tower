import enum

from sqlalchemy import (
    func,
    Column,
    Integer,
    BigInteger,
    String,
    Text,
    DateTime,
    ForeignKey,
    Enum,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.db import Base


class ClaimStatus(str, enum.Enum):
    active = "active"
    released = "released"
    expired = "expired"       # auto-released by the reaper (no heartbeat in time)
    invalidated = "invalidated"  # the claimed symbol no longer exists in the file


class ActivityType(str, enum.Enum):
    agent_registered = "agent_registered"
    claim_created = "claim_created"
    claim_released = "claim_released"
    claim_expired = "claim_expired"
    claim_invalidated = "claim_invalidated"
    claim_rejected = "claim_rejected"  # conflict detected, claim never granted
    message_sent = "message_sent"


class Project(Base):
    """
    A codebase multiple agents coordinate over. Claims and messages are
    scoped to a project so unrelated work (or a second hackathon team) never
    cross-talks.
    """

    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    repo_root = Column(Text, nullable=True)  # informational; not enforced server-side
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Agent(Base):
    """
    One participant -- typically one AI coding session (e.g. "claude-backend",
    "gpt-frontend"). An agent re-registers each time its harness starts a
    fresh session; last_seen_at is updated on every API call it makes, so a
    dead/abandoned agent is visible without needing an explicit "goodbye".
    """

    __tablename__ = "agents"
    __table_args__ = (Index("uq_project_agent_name", "project_id", "name", unique=True),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)      # e.g. "claude-backend"
    role = Column(String, nullable=True)       # e.g. "backend", "frontend" -- informational
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())

    project = relationship("Project")


class Claim(Base):
    """
    An agent's declared intent to work on a specific symbol (function/class/
    method) or, failing that, an explicit line range, in one file.

    IMPORTANT: line_start/line_end here are a SNAPSHOT taken at claim time,
    for display/audit purposes only. The actual conflict-detection logic
    (services/ast_conflict.py) never trusts these stored values -- it always
    re-parses the CURRENT file content and re-resolves symbol_name to a
    fresh line range at check time. This is deliberate: line numbers drift
    as code above them changes, so a cached range would silently go stale
    and either miss real conflicts or flag false ones. symbol_name is the
    stable identity; line numbers are always derived, never trusted.
    """

    __tablename__ = "claims"

    id = Column(BigInteger, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    file_path = Column(Text, nullable=False)          # relative to project repo_root
    symbol_name = Column(Text, nullable=True)          # qualified name, e.g. "ClassA.method_b"; null = explicit line-range claim
    line_start = Column(Integer, nullable=False)        # snapshot only -- see docstring
    line_end = Column(Integer, nullable=False)          # snapshot only -- see docstring
    note = Column(Text, nullable=True)                 # what the agent says it's doing
    status = Column(Enum(ClaimStatus), nullable=False, default=ClaimStatus.active)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_heartbeat_at = Column(DateTime(timezone=True), server_default=func.now())
    released_at = Column(DateTime(timezone=True), nullable=True)
    release_note = Column(Text, nullable=True)

    project = relationship("Project")
    agent = relationship("Agent")


# The conflict-check hot path is "all ACTIVE claims for this (project, file)".
Index("ix_claims_project_file_status", Claim.project_id, Claim.file_path, Claim.status)
Index("ix_claims_agent", Claim.agent_id)


class Message(Base):
    """Point-to-point (to_agent_id set) or broadcast (to_agent_id null) message."""

    __tablename__ = "messages"

    id = Column(BigInteger, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    from_agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    to_agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=True)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    project = relationship("Project")
    from_agent = relationship("Agent", foreign_keys=[from_agent_id])
    to_agent = relationship("Agent", foreign_keys=[to_agent_id])


Index("ix_messages_project_created", Message.project_id, Message.created_at)
Index("ix_messages_to_agent", Message.to_agent_id, Message.created_at)


class ActivityEvent(Base):
    """
    Unified, append-only feed of everything that happened -- what a newly
    joining or resuming agent reads to catch up on context it doesn't have.
    payload is a small JSON blob whose shape depends on `type`.
    """

    __tablename__ = "activity_events"

    id = Column(BigInteger, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    type = Column(Enum(ActivityType), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    payload = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    project = relationship("Project")
    agent = relationship("Agent")


Index("ix_activity_project_created", ActivityEvent.project_id, ActivityEvent.created_at)


class FileSnapshot(Base):
    """
    The latest content of a file as an agent sent it with a claim. The
    coordinator never reads anyone's disk; this is only what agents have
    shown it, kept so the dashboard can draw files and show claimed lines
    without an agent being online. It can lag behind the real file.
    """

    __tablename__ = "file_snapshots"
    __table_args__ = (Index("uq_snapshot_project_file", "project_id", "file_path", unique=True),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    file_path = Column(Text, nullable=False)
    source = Column(Text, nullable=False)
    line_count = Column(Integer, nullable=False)
    updated_by = Column(Integer, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())