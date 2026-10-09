from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app import config
from app.auth import (
    Principal,
    get_principal,
    issue_agent_token,
    join_key_matches,
    require_act_as,
    require_admin,
    require_project_access,
    rotate_join_key,
)
from app.db import get_db
from app.models import ActivityEvent, ActivityType, Agent, AgentToken, Project
from app.routers._guards import require_project
from app.schemas import AgentJoin, AgentOut, AgentRegister, AgentRegisteredOut, JoinOut

router = APIRouter(prefix="/agents", tags=["agents"])


def _register(db: Session, project_id: int, name: str, role: str | None) -> Agent:
    """Idempotent by (project, name): a fresh session of the same agent just refreshes last_seen_at."""
    existing = db.query(Agent).filter(Agent.project_id == project_id, Agent.name == name).one_or_none()
    if existing is not None:
        existing.last_seen_at = datetime.now(timezone.utc)
        if role is not None:
            existing.role = role
        return existing
    agent = Agent(project_id=project_id, name=name, role=role)
    db.add(agent)
    db.flush()
    db.add(ActivityEvent(project_id=project_id, type=ActivityType.agent_registered, agent_id=agent.id,
                         payload={"name": agent.name, "role": agent.role}))
    return agent


@router.post("/join", response_model=JoinOut)
def join(payload: AgentJoin, db: Session = Depends(get_db), x_join_key: str | None = Header(default=None)):
    """
    How an agent (its MCP server) enters a project, by project name.

    Auth off: the project is created if it doesn't exist; no token is issued.
    Auth on: the project must exist and the X-Join-Key header must match its
    join key; the response carries the agent's own bearer token.
    """
    auth = config.settings.auth_enabled
    project = db.query(Project).filter(Project.name == payload.project).one_or_none()
    if project is None:
        if auth:
            raise HTTPException(404, f"no project named {payload.project!r}; an admin has to create it first")
        project = Project(name=payload.project)
        rotate_join_key(db, project)
        db.add(project)
        db.flush()
    if auth and not join_key_matches(project, x_join_key):
        raise HTTPException(403, "wrong or missing join key (send it in the X-Join-Key header)")
    agent = _register(db, project.id, payload.name, payload.role)
    db.flush()
    token = issue_agent_token(db, agent) if auth else None
    db.commit()
    db.refresh(agent)
    return JoinOut(project_id=project.id, agent=AgentOut.model_validate(agent), token=token)


@router.post("", response_model=AgentRegisteredOut, status_code=201)
def register_agent(payload: AgentRegister, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    """Registers an agent by project id. Needs the admin token when auth is on (agents use /agents/join)."""
    require_admin(p)
    require_project(db, payload.project_id)
    agent = _register(db, payload.project_id, payload.name, payload.role)
    db.flush()
    token = issue_agent_token(db, agent) if config.settings.auth_enabled else None
    db.commit()
    db.refresh(agent)
    return AgentRegisteredOut.model_validate(agent).model_copy(update={"token": token})


@router.get("", response_model=list[AgentOut])
def list_agents(project_id: int, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    require_project_access(p, project_id)
    return db.query(Agent).filter(Agent.project_id == project_id).order_by(Agent.last_seen_at.desc()).all()


@router.get("/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: int, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")
    require_project_access(p, agent.project_id)
    return agent


@router.delete("/{agent_id}/tokens")
def revoke_tokens(agent_id: int, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    """Revokes every token the agent holds (the agent itself, or an admin)."""
    require_act_as(p, agent_id)
    if db.get(Agent, agent_id) is None:
        raise HTTPException(404, "agent not found")
    n = (
        db.query(AgentToken)
        .filter(AgentToken.agent_id == agent_id, AgentToken.revoked_at.is_(None))
        .update({AgentToken.revoked_at: datetime.now(timezone.utc)}, synchronize_session=False)
    )
    db.commit()
    return {"agent_id": agent_id, "revoked": n}