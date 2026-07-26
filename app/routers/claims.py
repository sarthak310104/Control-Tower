from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Claim, ClaimStatus, Agent
from app.schemas import ClaimCreate, ClaimOut, ClaimRelease
from app.services.claims import (
    ClaimRequest,
    create_claim,
    release_claim,
    heartbeat_claim,
    ClaimConflictError,
)

router = APIRouter(prefix="/claims", tags=["claims"])


def _touch_agent(db: Session, agent_id: int) -> None:
    agent = db.get(Agent, agent_id)
    if agent is not None:
        agent.last_seen_at = datetime.now(timezone.utc)


@router.post("", response_model=ClaimOut, status_code=201)
def new_claim(payload: ClaimCreate, db: Session = Depends(get_db)):
    _touch_agent(db, payload.agent_id)
    req = ClaimRequest(
        project_id=payload.project_id,
        agent_id=payload.agent_id,
        file_path=payload.file_path,
        source=payload.source,
        symbol_name=payload.symbol_name,
        line_start=payload.line_start,
        line_end=payload.line_end,
        note=payload.note,
    )
    try:
        return create_claim(db, req)
    except ClaimConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "conflicting_claim_id": exc.conflicting_claim.id,
                "conflicting_agent": exc.conflicting_agent_name,
                "file_path": exc.conflicting_claim.file_path,
                "symbol_name": exc.conflicting_claim.symbol_name,
                "line_start": exc.conflicting_claim.line_start,
                "line_end": exc.conflicting_claim.line_end,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/{claim_id}/release", response_model=ClaimOut)
def do_release(claim_id: int, payload: ClaimRelease, db: Session = Depends(get_db)):
    _touch_agent(db, payload.agent_id)
    try:
        return release_claim(db, claim_id, payload.agent_id, payload.release_note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{claim_id}/heartbeat", response_model=ClaimOut)
def do_heartbeat(claim_id: int, agent_id: int, db: Session = Depends(get_db)):
    _touch_agent(db, agent_id)
    try:
        return heartbeat_claim(db, claim_id, agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("", response_model=list[ClaimOut])
def list_claims(
    project_id: int,
    file_path: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Claim).filter(Claim.project_id == project_id)
    if file_path is not None:
        q = q.filter(Claim.file_path == file_path)
    if status is not None:
        q = q.filter(Claim.status == ClaimStatus(status))
    return q.order_by(Claim.created_at.desc()).all()
