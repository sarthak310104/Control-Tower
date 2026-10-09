from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Agent, Project


def require_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, f"project {project_id} not found")
    return project


def require_agent(db: Session, agent_id: int, project_id: int | None = None, touch: bool = True) -> Agent:
    """
    Turns what would otherwise be a foreign-key 500 into a clear 404/422,
    and stops an agent from acting inside a project it never joined.
    Also refreshes last_seen_at, since every call is proof of life.
    """
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, f"agent {agent_id} not found")
    if project_id is not None and agent.project_id != project_id:
        raise HTTPException(422, f"agent {agent_id} does not belong to project {project_id}")
    if touch:
        agent.last_seen_at = datetime.now(timezone.utc)
    return agent