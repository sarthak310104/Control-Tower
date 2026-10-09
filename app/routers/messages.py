from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.auth import Principal, get_principal, require_act_as, require_project_access
from app.db import get_db
from app.models import Message, ActivityEvent, ActivityType
from app.routers._guards import require_agent, require_project
from app.schemas import MessageCreate, MessageOut

router = APIRouter(prefix="/messages", tags=["messages"])


@router.post("", response_model=MessageOut, status_code=201)
def send_message(payload: MessageCreate, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    require_project_access(p, payload.project_id)
    require_act_as(p, payload.from_agent_id)
    if not payload.body.strip():
        raise HTTPException(422, "message body is empty")
    require_agent(db, payload.from_agent_id, payload.project_id)
    if payload.to_agent_id is not None:
        require_agent(db, payload.to_agent_id, payload.project_id, touch=False)

    message = Message(
        project_id=payload.project_id,
        from_agent_id=payload.from_agent_id,
        to_agent_id=payload.to_agent_id,
        body=payload.body,
    )
    db.add(message)
    db.flush()
    db.add(ActivityEvent(
        project_id=payload.project_id,
        type=ActivityType.message_sent,
        agent_id=payload.from_agent_id,
        payload={"message_id": message.id, "to_agent_id": payload.to_agent_id, "preview": payload.body[:120]},
    ))
    db.commit()
    db.refresh(message)
    return message


@router.get("/inbox", response_model=list[MessageOut])
def get_inbox(project_id: int, agent_id: int, since_id: int = 0, db: Session = Depends(get_db),
              p: Principal = Depends(get_principal)):
    """
    Messages directed at this agent specifically, or broadcast to the whole
    project, with id > since_id. An agent polls this with the highest id it
    has already seen as since_id -- simple, robust cursor, no timestamps/
    clock-skew concerns across machines. An agent's own broadcasts are
    excluded: it already knows what it said.
    """
    require_project_access(p, project_id)
    require_act_as(p, agent_id)
    require_agent(db, agent_id, project_id)
    db.commit()
    return (
        db.query(Message)
        .filter(
            Message.project_id == project_id,
            Message.id > since_id,
            or_(
                Message.to_agent_id == agent_id,
                and_(Message.to_agent_id.is_(None), Message.from_agent_id != agent_id),
            ),
        )
        .order_by(Message.id.asc())
        .all()
    )


@router.get("", response_model=list[MessageOut])
def list_messages(project_id: int, since_id: int = 0, limit: int = 100, db: Session = Depends(get_db),
                  p: Principal = Depends(get_principal)):
    """Project-wide message feed (every direct + broadcast message), for dashboards and audit."""
    require_project_access(p, project_id)
    require_project(db, project_id)
    return (
        db.query(Message)
        .filter(Message.project_id == project_id, Message.id > since_id)
        .order_by(Message.id.asc())
        .limit(min(limit, 500))
        .all()
    )