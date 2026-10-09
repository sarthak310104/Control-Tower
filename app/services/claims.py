from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Claim, ClaimStatus, ActivityEvent, ActivityType, Agent, FileSnapshot
from app.services.ast_conflict import (
    resolve_range,
    ranges_overlap,
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


@dataclass
class Conflict:
    claim: Claim
    agent_name: str
    line_start: int  # freshly re-resolved against the current source, not the stored snapshot
    line_end: int


def _log_activity(db: Session, project_id: int, type_: ActivityType, agent_id: int | None, payload: dict) -> None:
    db.add(ActivityEvent(project_id=project_id, type=type_, agent_id=agent_id, payload=payload))


def _lock_file(db: Session, project_id: int, file_path: str) -> None:
    """
    Serializes check-then-insert for one (project, file) pair.

    Without this, two agents claiming the same function at the same instant
    can both read "no active claims", both pass the overlap check, and both
    insert -- a classic TOCTOU race. A row lock can't help because the row
    that would conflict doesn't exist yet, so this takes a transaction-scoped
    Postgres advisory lock keyed on a hash of (project, file). It is released
    automatically on commit/rollback, and claims on *different* files never
    contend with each other.
    """
    digest = hashlib.blake2b(f"{project_id}:{file_path}".encode(), digest_size=8).digest()
    key = int.from_bytes(digest, "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})


def _record_snapshot(db: Session, req: ClaimRequest) -> None:
    """Keeps the latest file content an agent sent, for the dashboard (see FileSnapshot)."""
    snap = (
        db.query(FileSnapshot)
        .filter(FileSnapshot.project_id == req.project_id, FileSnapshot.file_path == req.file_path)
        .one_or_none()
    )
    lines = req.source.count("\n") + (0 if req.source.endswith("\n") or not req.source else 1)
    if snap is None:
        db.add(FileSnapshot(project_id=req.project_id, file_path=req.file_path, source=req.source,
                            line_count=lines, updated_by=req.agent_id))
    elif snap.source != req.source:
        snap.source, snap.line_count, snap.updated_by = req.source, lines, req.agent_id
        snap.updated_at = datetime.now(timezone.utc)


def _resolve_requested(req: ClaimRequest) -> tuple[int, int]:
    try:
        return resolve_range(req.source, req.symbol_name, req.line_start, req.line_end)
    except UnparsableFileError:
        raise ValueError(
            "file content is not valid Python right now (likely another agent mid-edit) -- "
            "retry shortly, or claim an explicit line range instead of a symbol name"
        )
    except SymbolNotFoundError as exc:
        raise ValueError(str(exc))


def find_conflicts(
    db: Session,
    req: ClaimRequest,
    requested: tuple[int, int],
    invalidate_stale: bool,
) -> list[Conflict]:
    """
    Re-resolves every OTHER agent's active claim on this file against the
    request's CURRENT source and returns the ones that overlap `requested`.

    A symbol claim whose symbol is genuinely gone from a parseable file is
    stale: if invalidate_stale, it's marked invalidated (caller commits);
    otherwise it's just skipped. If the file is currently unparseable we
    can't tell "deleted" from "mid-edit", so we fall back to the claim's
    stored line snapshot and keep it alive -- the conservative choice.
    """
    existing_claims = (
        db.query(Claim)
        .filter(
            Claim.project_id == req.project_id,
            Claim.file_path == req.file_path,
            Claim.status == ClaimStatus.active,
            Claim.agent_id != req.agent_id,  # an agent's own claims never conflict with itself
        )
        .all()
    )

    conflicts: list[Conflict] = []
    for existing in existing_claims:
        if existing.symbol_name is not None:
            try:
                fresh_start, fresh_end = resolve_range(req.source, existing.symbol_name, None, None)
            except UnparsableFileError:
                fresh_start, fresh_end = existing.line_start, existing.line_end
            except SymbolNotFoundError:
                if invalidate_stale:
                    existing.status = ClaimStatus.invalidated
                    existing.released_at = datetime.now(timezone.utc)
                    existing.release_note = "auto-invalidated: symbol no longer found in current file"
                    _log_activity(
                        db, req.project_id, ActivityType.claim_invalidated, existing.agent_id,
                        {"claim_id": existing.id, "file_path": existing.file_path,
                         "symbol_name": existing.symbol_name},
                    )
                continue
        else:
            fresh_start, fresh_end = existing.line_start, existing.line_end

        if ranges_overlap(requested[0], requested[1], fresh_start, fresh_end):
            agent = db.get(Agent, existing.agent_id)
            conflicts.append(Conflict(existing, agent.name if agent else "unknown-agent", fresh_start, fresh_end))
    return conflicts


def check_claim(db: Session, req: ClaimRequest) -> tuple[tuple[int, int], list[Conflict]]:
    """Dry run: what create_claim would do, with no writes at all."""
    requested = _resolve_requested(req)
    return requested, find_conflicts(db, req, requested, invalidate_stale=False)


def create_claim(db: Session, req: ClaimRequest) -> Claim:
    """
    Resolves the requested symbol/range against req.source, checks it
    against every other ACTIVE claim on the same file (re-resolving each of
    THEM against the same current source too -- this is also where a claim
    whose symbol has since disappeared gets caught and invalidated), and
    either creates the claim or raises ClaimConflictError.
    """
    requested = _resolve_requested(req)
    _lock_file(db, req.project_id, req.file_path)
    _record_snapshot(db, req)

    conflicts = find_conflicts(db, req, requested, invalidate_stale=True)
    if conflicts:
        first = conflicts[0]
        _log_activity(
            db, req.project_id, ActivityType.claim_rejected, req.agent_id,
            {"file_path": req.file_path, "symbol_name": req.symbol_name,
             "conflicting_claim_id": first.claim.id, "conflicting_agent": first.agent_name},
        )
        db.commit()  # persist invalidations + the rejection event even though we're refusing
        raise ClaimConflictError(first.claim, first.agent_name)

    claim = Claim(
        project_id=req.project_id,
        agent_id=req.agent_id,
        file_path=req.file_path,
        symbol_name=req.symbol_name,
        line_start=requested[0],
        line_end=requested[1],
        note=req.note,
        status=ClaimStatus.active,
    )
    db.add(claim)
    db.flush()
    _log_activity(
        db, req.project_id, ActivityType.claim_created, req.agent_id,
        {"claim_id": claim.id, "file_path": claim.file_path, "symbol_name": claim.symbol_name,
         "line_start": requested[0], "line_end": requested[1], "note": req.note},
    )
    db.commit()
    db.refresh(claim)
    return claim


def _owned_active_claim(db: Session, claim_id: int, agent_id: int) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise LookupError(f"claim {claim_id} not found")
    if claim.status != ClaimStatus.active:
        raise ValueError(f"claim {claim_id} is not active (status={claim.status.value})")
    if claim.agent_id != agent_id:
        raise ValueError(f"claim {claim_id} is owned by a different agent")
    return claim


def release_claim(db: Session, claim_id: int, agent_id: int, release_note: str | None) -> Claim:
    claim = _owned_active_claim(db, claim_id, agent_id)
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
    claim = _owned_active_claim(db, claim_id, agent_id)
    claim.last_heartbeat_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(claim)
    return claim


def heartbeat_agent(db: Session, agent_id: int) -> int:
    """Refreshes every active claim this agent holds in one statement; returns how many."""
    now = datetime.now(timezone.utc)
    count = (
        db.query(Claim)
        .filter(Claim.agent_id == agent_id, Claim.status == ClaimStatus.active)
        .update({Claim.last_heartbeat_at: now}, synchronize_session=False)
    )
    db.commit()
    return count


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
        .with_for_update(skip_locked=True)
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
    db.commit()
    return stale