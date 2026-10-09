from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    name: str
    repo_root: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    repo_root: str | None
    created_at: datetime


class AgentRegister(BaseModel):
    project_id: int
    name: str
    role: str | None = None


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    name: str
    role: str | None
    last_seen_at: datetime


class ClaimCreate(BaseModel):
    project_id: int
    agent_id: int
    file_path: str
    source: str
    symbol_name: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    note: str | None = None


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
    release_note: str | None = None


class MessageCreate(BaseModel):
    project_id: int
    from_agent_id: int
    to_agent_id: int | None = None  # null = broadcast to the whole project
    body: str


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
    source: str


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
    symbols: list[SymbolOut] | None  # None when the file isn't parseable Python