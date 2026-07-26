from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Agent, ActivityEvent, ActivityType
from app.schemas import AgentRegister, AgentOut

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", response_model=AgentOut, status_code=201)
def register_agent(payload: AgentRegister, db: Session = Depends(get_db)):
    """
    Idempotent by (project_id, name): calling this again with the same name
    (e.g. a fresh session of "claude-backend" starting up) just refreshes
    last_seen_at instead of erroring or creating a duplicate.
    """
    existing = (
        db.query(Agent)
        .filter(Agent.project_id == payload.project_id, Agent.name == payload.name)
        .one_or_none()
    )
    if existing is not None:
        existing.last_seen_at = datetime.now(timezone.utc)
        if payload.role is not None:
            existing.role = payload.role
        db.commit()
        db.refresh(existing)
        return existing

    agent = Agent(project_id=payload.project_id, name=payload.name, role=payload.role)
    db.add(agent)
    db.flush()
    db.add(ActivityEvent(
        project_id=payload.project_id,
        type=ActivityType.agent_registered,
        agent_id=agent.id,
        payload={"name": agent.name, "role": agent.role},
    ))
    db.commit()
    db.refresh(agent)
    return agent


@router.get("", response_model=list[AgentOut])
def list_agents(project_id: int, db: Session = Depends(get_db)):
    return db.query(Agent).filter(Agent.project_id == project_id).order_by(Agent.last_seen_at.desc()).all()


@router.get("/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: int, db: Session = Depends(get_db)):
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")
    return agent
