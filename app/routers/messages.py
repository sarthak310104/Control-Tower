from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Message, Agent, ActivityEvent, ActivityType
from app.schemas import MessageCreate, MessageOut

router = APIRouter(prefix="/messages", tags=["messages"])


@router.post("", response_model=MessageOut, status_code=201)
def send_message(payload: MessageCreate, db: Session = Depends(get_db)):
    agent = db.get(Agent, payload.from_agent_id)
    if agent is not None:
        agent.last_seen_at = datetime.now(timezone.utc)

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
def get_inbox(project_id: int, agent_id: int, since_id: int = 0, db: Session = Depends(get_db)):
    """
    Messages directed at this agent specifically, or broadcast to the whole
    project, with id > since_id. An agent polls this with the highest id it
    has already seen as since_id -- simple, robust cursor, no timestamps/
    clock-skew concerns across machines.
    """
    return (
        db.query(Message)
        .filter(
            Message.project_id == project_id,
            Message.id > since_id,
            or_(Message.to_agent_id == agent_id, Message.to_agent_id.is_(None)),
        )
        .order_by(Message.id.asc())
        .all()
    )
