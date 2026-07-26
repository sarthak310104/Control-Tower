from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Claim, ClaimStatus, ActivityEvent, ActivityType, Agent
from app.services.ast_conflict import (
    resolve_range,
    SymbolNotFoundError,
    UnparsableFileError,
)


class ClaimConflictError(Exception):
    """Raised when a requested claim overlaps an existing active claim."""

    def __init__(self, conflicting_claim: Claim, conflicting_agent_name: str):
        self.conflicting_claim = conflicting_claim
        self.conflicting_agent_name = conflicting_agent_name
        super().__init__(
            f"conflicts with active claim #{conflicting_claim.id} by "
            f"'{conflicting_agent_name}' on {conflicting_claim.file_path} "
            f"({conflicting_claim.symbol_name or f'lines {conflicting_claim.line_start}-{conflicting_claim.line_end}'})"
        )


@dataclass
class ClaimRequest:
    project_id: int
    agent_id: int
    file_path: str
    source: str  # current content of the file, sent by the client -- see module docstring in ast_conflict.py
    symbol_name: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    note: str | None = None


def _log_activity(db: Session, project_id: int, type_: ActivityType, agent_id: int | None, payload: dict) -> None:
    db.add(ActivityEvent(project_id=project_id, type=type_, agent_id=agent_id, payload=payload))


def create_claim(db: Session, req: ClaimRequest) -> Claim:
    """
    Resolves the requested symbol/range against req.source, checks it
    against every other ACTIVE claim on the same file (re-resolving each of
    THEM against the same current source too -- this is also where a claim
    whose symbol has since disappeared gets caught and invalidated), and
    either creates the claim or raises ClaimConflictError.
    """
    try:
        requested_start, requested_end = resolve_range(
            req.source, req.symbol_name, req.line_start, req.line_end
        )
    except UnparsableFileError:
        raise ValueError(
            "file content is not valid Python right now (likely another agent mid-edit) -- "
            "retry shortly, or claim an explicit line range instead of a symbol name"
        )
    except SymbolNotFoundError as exc:
        raise ValueError(str(exc))

    existing_claims = (
        db.query(Claim)
        .filter(
            Claim.project_id == req.project_id,
            Claim.file_path == req.file_path,
            Claim.status == ClaimStatus.active,
        )
        .all()
    )

    for existing in existing_claims:
        if existing.agent_id == req.agent_id:
            continue  # an agent's own claims never conflict with itself

        if existing.symbol_name is not None:
            try:
                fresh_start, fresh_end = resolve_range(req.source, existing.symbol_name, None, None)
            except (SymbolNotFoundError, UnparsableFileError):
                # The symbol this claim was made against no longer exists in
                # the current file -- it's stale, invalidate it rather than
                # let it block new work indefinitely.
                existing.status = ClaimStatus.invalidated
                existing.released_at = datetime.now(timezone.utc)
                existing.release_note = "auto-invalidated: symbol no longer found in current file"
                _log_activity(
                    db, req.project_id, ActivityType.claim_invalidated, existing.agent_id,
                    {"claim_id": existing.id, "file_path": existing.file_path, "symbol_name": existing.symbol_name},
                )
                continue
        else:
            fresh_start, fresh_end = existing.line_start, existing.line_end

        if requested_start <= fresh_end and fresh_start <= requested_end:
            db.commit()  # persist any invalidations found above even though we're about to reject
            agent = db.get(Agent, existing.agent_id)
            raise ClaimConflictError(existing, agent.name if agent else "unknown-agent")

    claim = Claim(
        project_id=req.project_id,
        agent_id=req.agent_id,
        file_path=req.file_path,
        symbol_name=req.symbol_name,
        line_start=requested_start,
        line_end=requested_end,
        note=req.note,
        status=ClaimStatus.active,
    )
    db.add(claim)
    db.flush()
    _log_activity(
        db, req.project_id, ActivityType.claim_created, req.agent_id,
        {"claim_id": claim.id, "file_path": claim.file_path, "symbol_name": claim.symbol_name,
         "line_start": requested_start, "line_end": requested_end},
    )
    db.commit()
    db.refresh(claim)
    return claim


def release_claim(db: Session, claim_id: int, agent_id: int, release_note: str | None) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise ValueError(f"claim {claim_id} not found")
    if claim.status != ClaimStatus.active:
        raise ValueError(f"claim {claim_id} is not active (status={claim.status.value})")
    if claim.agent_id != agent_id:
        raise ValueError(f"claim {claim_id} is owned by a different agent")

    claim.status = ClaimStatus.released
    claim.released_at = datetime.now(timezone.utc)
    claim.release_note = release_note
    _log_activity(
        db, claim.project_id, ActivityType.claim_released, agent_id,
        {"claim_id": claim.id, "file_path": claim.file_path, "symbol_name": claim.symbol_name, "note": release_note},
    )
    db.commit()
    db.refresh(claim)
    return claim


def heartbeat_claim(db: Session, claim_id: int, agent_id: int) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise ValueError(f"claim {claim_id} not found")
    if claim.status != ClaimStatus.active:
        raise ValueError(f"claim {claim_id} is not active (status={claim.status.value})")
    if claim.agent_id != agent_id:
        raise ValueError(f"claim {claim_id} is owned by a different agent")

    claim.last_heartbeat_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(claim)
    return claim


def reap_expired_claims(db: Session) -> list[Claim]:
    """
    Sweeps ACTIVE claims with no heartbeat within the TTL window and marks
    them expired. Called periodically by the background reaper (see
    services/reaper.py) so a crashed agent or an ended session doesn't hold
    a lock forever.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.claim_ttl_seconds)
    stale = (
        db.query(Claim)
        .filter(Claim.status == ClaimStatus.active, Claim.last_heartbeat_at < cutoff)
        .all()
    )
    for claim in stale:
        claim.status = ClaimStatus.expired
        claim.released_at = datetime.now(timezone.utc)
        claim.release_note = f"auto-expired: no heartbeat for {settings.claim_ttl_seconds}s"
        _log_activity(
            db, claim.project_id, ActivityType.claim_expired, claim.agent_id,
            {"claim_id": claim.id, "file_path": claim.file_path, "symbol_name": claim.symbol_name},
        )
    if stale:
        db.commit()
    return stale
