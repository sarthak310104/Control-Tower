from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings

NAME = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")
PATH = Field(min_length=1, max_length=1024)
SOURCE = Field(max_length=settings.max_source_chars)
NOTE = Field(default=None, max_length=2000)


class ProjectCreate(BaseModel):
    name: str = NAME
    repo_root: str | None = Field(default=None, max_length=1024)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    repo_root: str | None
    created_at: datetime


class ProjectCreatedOut(ProjectOut):
    # Shown only when the project is first created or its key is rotated; agents need it to join.
    join_key: str | None = None


class JoinKeyOut(BaseModel):
    project_id: int
    join_key: str


class AgentRegister(BaseModel):
    project_id: int
    name: str = NAME
    role: str | None = Field(default=None, max_length=60)


class AgentJoin(BaseModel):
    project: str = NAME  # project name; created on join only when auth is off
    name: str = NAME
    role: str | None = Field(default=None, max_length=60)


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    name: str
    role: str | None
    last_seen_at: datetime


class AgentRegisteredOut(AgentOut):
    token: str | None = None  # the agent's bearer token when auth is on; shown once


class JoinOut(BaseModel):
    project_id: int
    agent: AgentOut
    token: str | None  # None when auth is off


class ClaimCreate(BaseModel):
    project_id: int
    agent_id: int
    file_path: str = PATH
    source: str = SOURCE
    symbol_name: str | None = Field(default=None, max_length=300)
    line_start: int | None = None
    line_end: int | None = None
    note: str | None = NOTE


class ClaimOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    agent_id: int
    file_path: str
    symbol_name: str | None
    line_start: int
    line_end: int
    note: str | None
    status: str
    created_at: datetime
    last_heartbeat_at: datetime
    released_at: datetime | None
    release_note: str | None


class ClaimRelease(BaseModel):
    agent_id: int
    release_note: str | None = NOTE


class MessageCreate(BaseModel):
    project_id: int
    from_agent_id: int
    to_agent_id: int | None = None  # null = broadcast to the whole project
    body: str = Field(max_length=10_000)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    from_agent_id: int
    to_agent_id: int | None
    body: str
    created_at: datetime


class ActivityEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    type: str
    agent_id: int | None
    payload: dict
    created_at: datetime


class ConflictOut(BaseModel):
    claim_id: int
    agent_name: str
    symbol_name: str | None
    line_start: int  # re-resolved against the source you sent, not the stored snapshot
    line_end: int
    note: str | None


class ClaimCheckOut(BaseModel):
    file_path: str
    symbol_name: str | None
    line_start: int
    line_end: int
    ok: bool
    conflicts: list[ConflictOut]


class AgentHeartbeatOut(BaseModel):
    agent_id: int
    claims_refreshed: int


class SymbolsRequest(BaseModel):
    source: str = SOURCE
    file_path: str | None = Field(default=None, max_length=1024)  # picks the language; Python when omitted


class SymbolOut(BaseModel):
    symbol: str
    line_start: int
    line_end: int


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    file_path: str
    line_count: int
    updated_by: int | None
    updated_at: datetime


class FileContentOut(FileOut):
    source: str
    symbols: list[SymbolOut] | None  # None when the file doesn't parse or its language has no symbol support


class ReconcileFile(BaseModel):
    file_path: str = PATH
    source: str | None = Field(default=None, max_length=settings.max_source_chars)  # None for a deleted file
    changed: list[tuple[int, int]] = Field(default_factory=list, max_length=5000)  # line ranges in `source`
    status: str = Field(default="modified", pattern="^(modified|added|deleted)$")


class ReconcileRequest(BaseModel):
    project_id: int
    agent_id: int
    base: str | None = Field(default=None, max_length=200)  # informational: the git ref the diff was taken against
    files: list[ReconcileFile] = Field(max_length=500)


class OverlapOut(BaseModel):
    claim_id: int
    agent_id: int
    agent: str
    target: str


class FindingOut(BaseModel):
    kind: str  # edit_unclaimed | edit_conflict
    file_path: str
    ranges: list[tuple[int, int]]
    symbols: list[str]
    overlaps: list[OverlapOut]
    new: bool  # False if this exact finding was already reported


class ReconcileOut(BaseModel):
    clean: bool
    files_checked: int
    lines_changed: int
    lines_covered: int
    findings: list[FindingOut]
    added: list[str]
    deleted: list[str]