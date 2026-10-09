from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Claim, ClaimStatus
from app.routers._guards import require_agent, require_project
from app.schemas import (
    AgentHeartbeatOut,
    ClaimCheckOut,
    ClaimCreate,
    ClaimOut,
    ClaimRelease,
    ConflictOut,
    SymbolOut,
    SymbolsRequest,
)
from app.services.ast_conflict import UnparsableFileError, extract_symbols
from app.services.claims import (
    ClaimConflictError,
    ClaimRequest,
    check_claim,
    create_claim,
    heartbeat_agent,
    heartbeat_claim,
    release_claim,
)

router = APIRouter(prefix="/claims", tags=["claims"])


def _to_request(payload: ClaimCreate) -> ClaimRequest:
    return ClaimRequest(
        project_id=payload.project_id,
        agent_id=payload.agent_id,
        file_path=payload.file_path,
        source=payload.source,
        symbol_name=payload.symbol_name,
        line_start=payload.line_start,
        line_end=payload.line_end,
        note=payload.note,
    )


@router.post("", response_model=ClaimOut, status_code=201)
def new_claim(payload: ClaimCreate, db: Session = Depends(get_db)):
    require_agent(db, payload.agent_id, payload.project_id)
    try:
        return create_claim(db, _to_request(payload))
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
                "note": exc.conflicting_claim.note,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/check", response_model=ClaimCheckOut)
def dry_run_claim(payload: ClaimCreate, db: Session = Depends(get_db)):
    """
    Same resolution + overlap logic as POST /claims, but writes nothing: no
    claim, no invalidations, no activity events. Lets an agent ask "would
    this collide, and with whom?" before committing to a plan.
    """
    require_agent(db, payload.agent_id, payload.project_id, touch=False)
    try:
        (start, end), conflicts = check_claim(db, _to_request(payload))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.rollback()
    return ClaimCheckOut(
        file_path=payload.file_path,
        symbol_name=payload.symbol_name,
        line_start=start,
        line_end=end,
        ok=not conflicts,
        conflicts=[
            ConflictOut(
                claim_id=c.claim.id,
                agent_name=c.agent_name,
                symbol_name=c.claim.symbol_name,
                line_start=c.line_start,
                line_end=c.line_end,
                note=c.claim.note,
            )
            for c in conflicts
        ],
    )


@router.post("/symbols", response_model=list[SymbolOut])
def list_symbols(payload: SymbolsRequest):
    """The claimable symbols in a piece of Python source, with current line spans. Stateless."""
    try:
        symbols = extract_symbols(payload.source)
    except UnparsableFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [SymbolOut(symbol=s.qualified_name, line_start=s.line_start, line_end=s.line_end) for s in symbols.values()]


@router.post("/{claim_id}/release", response_model=ClaimOut)
def do_release(claim_id: int, payload: ClaimRelease, db: Session = Depends(get_db)):
    require_agent(db, payload.agent_id)
    try:
        return release_claim(db, claim_id, payload.agent_id, payload.release_note)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{claim_id}/heartbeat", response_model=ClaimOut)
def do_heartbeat(claim_id: int, agent_id: int, db: Session = Depends(get_db)):
    require_agent(db, agent_id)
    try:
        return heartbeat_claim(db, claim_id, agent_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/heartbeat", response_model=AgentHeartbeatOut)
def do_agent_heartbeat(agent_id: int, db: Session = Depends(get_db)):
    """Refresh every active claim an agent holds in one call (what the MCP layer's timer uses)."""
    require_agent(db, agent_id)
    return AgentHeartbeatOut(agent_id=agent_id, claims_refreshed=heartbeat_agent(db, agent_id))


@router.get("", response_model=list[ClaimOut])
def list_claims(
    project_id: int,
    file_path: str | None = None,
    status: ClaimStatus | None = None,
    agent_id: int | None = None,
    db: Session = Depends(get_db),
):
    require_project(db, project_id)
    q = db.query(Claim).filter(Claim.project_id == project_id)
    if file_path is not None:
        q = q.filter(Claim.file_path == file_path)
    if status is not None:
        q = q.filter(Claim.status == status)
    if agent_id is not None:
        q = q.filter(Claim.agent_id == agent_id)
    return q.order_by(Claim.created_at.desc()).all()