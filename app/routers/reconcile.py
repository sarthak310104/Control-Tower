from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import Principal, get_principal, require_act_as, require_project_access
from app.db import get_db
from app.routers._guards import require_agent
from app.schemas import FindingOut, OverlapOut, ReconcileOut, ReconcileRequest
from app.services.reconcile import FileChange, reconcile

router = APIRouter(prefix="/reconcile", tags=["reconcile"])


@router.post("", response_model=ReconcileOut)
def do_reconcile(payload: ReconcileRequest, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    """
    Checks an agent's actual changes (from its local `git diff`) against its
    claims. Lines it changed without a claim are logged as edit_unclaimed;
    lines inside another agent's active claim as edit_conflict. The MCP
    server's `reconcile` tool builds this request; see services/reconcile.py.
    """
    require_project_access(p, payload.project_id)
    require_act_as(p, payload.agent_id)
    require_agent(db, payload.agent_id, payload.project_id)
    report = reconcile(
        db, payload.project_id, payload.agent_id,
        [FileChange(f.file_path, f.source, list(f.changed), f.status) for f in payload.files],
        payload.base,
    )
    return ReconcileOut(
        clean=report.clean,
        files_checked=report.files_checked,
        lines_changed=report.lines_changed,
        lines_covered=report.lines_covered,
        findings=[
            FindingOut(kind=f.kind, file_path=f.file_path, ranges=f.ranges, symbols=f.symbols, new=f.reported,
                       overlaps=[OverlapOut(claim_id=o.claim_id, agent_id=o.agent_id, agent=o.agent_name, target=o.target)
                                 for o in f.overlaps])
            for f in report.findings
        ],
        added=report.added,
        deleted=report.deleted,
    )