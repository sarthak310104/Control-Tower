"""
Git reconciliation: compare what an agent actually changed with what it
claimed.

Claims alone are advisory: nothing stops an agent editing code it never
claimed. Reconciliation closes that loop. The agent's MCP server runs
`git diff` locally (the coordinator never touches anyone's disk) and sends,
per changed file, the file's current content plus the changed line ranges
in that content. For each changed line we ask:

1. Was it covered by one of this agent's own claims on that file, either
   still active or ended within RECONCILE_WINDOW_SECONDS? (Edits usually land
   right before a release, and an agent may reconcile just after releasing.)
2. If not, did it fall inside another agent's ACTIVE claim? That's the bad
   case: the agent edited code someone else had reserved.

Claim ranges are re-resolved against the content being reconciled, by
symbol name, exactly like conflict checks, so they line up with the diff.

Uncovered lines become `edit_unclaimed` events, or `edit_conflict` when they
overlap another agent's claim. Each finding has a fingerprint (file + the
changed lines' text) so reconciling again doesn't report it twice.

New files and deleted files are listed but not flagged: nobody can hold a
claim on code that didn't exist yet, and a deleted file has no content to
resolve symbols against.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ActivityEvent, ActivityType, Agent, Claim, ClaimStatus, FileSnapshot
from app.services.ast_conflict import (
    SymbolNotFoundError,
    UnparsableFileError,
    UnsupportedLanguageError,
    extract_symbols,
    resolve_range,
)


@dataclass
class FileChange:
    file_path: str
    source: str | None          # current content; None for a deleted file
    changed: list[tuple[int, int]] = field(default_factory=list)  # 1-based inclusive, in `source`
    status: str = "modified"    # modified | added | deleted


@dataclass
class Overlap:
    claim_id: int
    agent_id: int
    agent_name: str
    target: str


@dataclass
class Finding:
    file_path: str
    ranges: list[tuple[int, int]]
    symbols: list[str]
    overlaps: list[Overlap]
    fingerprint: str
    reported: bool  # False when an identical finding was already reported earlier

    @property
    def kind(self) -> str:
        return "edit_conflict" if self.overlaps else "edit_unclaimed"


@dataclass
class Report:
    files_checked: int
    lines_changed: int
    lines_covered: int
    findings: list[Finding]
    added: list[str]
    deleted: list[str]

    @property
    def clean(self) -> bool:
        return not self.findings


def merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sorts and merges overlapping or adjacent closed ranges."""
    out: list[list[int]] = []
    for a, b in sorted((min(a, b), max(a, b)) for a, b in ranges):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def subtract(ranges: list[tuple[int, int]], cover: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The parts of `ranges` not covered by `cover`."""
    result = []
    cover = merge(cover)
    for a, b in merge(ranges):
        cur = a
        for c, d in cover:
            if d < cur or c > b:
                continue
            if c > cur:
                result.append((cur, c - 1))
            cur = max(cur, d + 1)
            if cur > b:
                break
        if cur <= b:
            result.append((cur, b))
    return result


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _claim_range(claim: Claim, source: str, file_path: str) -> tuple[int, int]:
    """Where the claim sits in `source` now; its stored snapshot if the symbol can't be resolved."""
    if claim.symbol_name:
        try:
            return resolve_range(source, claim.symbol_name, None, None, file_path)
        except (SymbolNotFoundError, UnparsableFileError, UnsupportedLanguageError):
            pass
    return claim.line_start, claim.line_end


def _target(claim: Claim) -> str:
    return claim.symbol_name or f"lines {claim.line_start}-{claim.line_end}"


def _symbols_touched(source: str, file_path: str, ranges: list[tuple[int, int]]) -> list[str]:
    try:
        symbols = extract_symbols(source, file_path)
    except (UnparsableFileError, UnsupportedLanguageError):
        return []
    touched = []
    for a, b in ranges:
        for line in range(a, b + 1):
            # innermost symbol containing the line
            hits = [s for s in symbols.values() if s.line_start <= line <= s.line_end]
            if hits:
                name = min(hits, key=lambda s: s.line_end - s.line_start).qualified_name
                if name not in touched:
                    touched.append(name)
    return touched


def _fingerprint(file_path: str, source: str, ranges: list[tuple[int, int]]) -> str:
    lines = source.split("\n")
    text = "\n".join("\n".join(lines[a - 1:b]) for a, b in ranges)
    return hashlib.sha256(f"{file_path}\0{text}".encode()).hexdigest()[:32]


def reconcile(db: Session, project_id: int, agent_id: int, changes: list[FileChange], base: str | None = None) -> Report:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(seconds=settings.reconcile_window_seconds)
    findings: list[Finding] = []
    added, deleted = [], []
    lines_changed = lines_covered = files_checked = 0

    for ch in changes:
        if ch.status == "deleted" or ch.source is None:
            deleted.append(ch.file_path)
            continue
        if ch.status == "added":
            added.append(ch.file_path)
            _remember(db, project_id, agent_id, ch)
            continue
        changed = merge([(max(1, a), max(1, b)) for a, b in ch.changed])
        if not changed:
            continue
        files_checked += 1
        _remember(db, project_id, agent_id, ch)

        claims = (
            db.query(Claim)
            .filter(Claim.project_id == project_id, Claim.file_path == ch.file_path)
            .filter(or_(Claim.status == ClaimStatus.active, Claim.released_at >= window_start))
            .all()
        )
        mine = [_claim_range(c, ch.source, ch.file_path) for c in claims if c.agent_id == agent_id]
        others = [(c, _claim_range(c, ch.source, ch.file_path)) for c in claims
                  if c.agent_id != agent_id and c.status == ClaimStatus.active]

        uncovered = subtract(changed, mine)
        total = sum(b - a + 1 for a, b in changed)
        lines_changed += total
        lines_covered += total - sum(b - a + 1 for a, b in uncovered)
        if not uncovered:
            continue

        overlaps = []
        for claim, rng in others:
            if any(_overlaps(u, rng) for u in uncovered):
                holder = db.get(Agent, claim.agent_id)
                overlaps.append(Overlap(claim.id, claim.agent_id, holder.name if holder else "unknown-agent", _target(claim)))
        finding = Finding(
            file_path=ch.file_path,
            ranges=uncovered,
            symbols=_symbols_touched(ch.source, ch.file_path, uncovered),
            overlaps=overlaps,
            fingerprint=_fingerprint(ch.file_path, ch.source, uncovered),
            reported=True,
        )
        finding.reported = _report(db, project_id, agent_id, finding, base)
        findings.append(finding)

    db.commit()
    return Report(files_checked, lines_changed, lines_covered, findings, added, deleted)


def _remember(db: Session, project_id: int, agent_id: int, ch: FileChange) -> None:
    """Reconciled content is the freshest copy of the file the coordinator has; keep it for the dashboard."""
    snap = (
        db.query(FileSnapshot)
        .filter(FileSnapshot.project_id == project_id, FileSnapshot.file_path == ch.file_path)
        .one_or_none()
    )
    if snap is None:
        return  # only refresh files the coordinator already tracks; don't import a whole repo
    if snap.source != ch.source:
        snap.source = ch.source
        snap.line_count = ch.source.count("\n") + (0 if ch.source.endswith("\n") or not ch.source else 1)
        snap.updated_by = agent_id
        snap.updated_at = datetime.now(timezone.utc)


def _report(db: Session, project_id: int, agent_id: int, f: Finding, base: str | None) -> bool:
    """Logs the finding to the activity feed unless the same one was already logged. Returns True if new."""
    seen = (
        db.query(ActivityEvent.id)
        .filter(
            ActivityEvent.project_id == project_id,
            ActivityEvent.agent_id == agent_id,
            ActivityEvent.type.in_([ActivityType.edit_unclaimed, ActivityType.edit_conflict]),
            ActivityEvent.payload["fingerprint"].astext == f.fingerprint,
        )
        .first()
    )
    if seen is not None:
        return False
    db.add(ActivityEvent(
        project_id=project_id,
        type=ActivityType(f.kind),
        agent_id=agent_id,
        payload={
            "file_path": f.file_path,
            "ranges": [list(r) for r in f.ranges],
            "symbols": f.symbols,
            "overlaps": [{"claim_id": o.claim_id, "agent_id": o.agent_id, "agent": o.agent_name, "target": o.target}
                         for o in f.overlaps],
            "fingerprint": f.fingerprint,
            "base": base,
        },
    ))
    return True