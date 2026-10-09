from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ActivityEvent
from app.routers._guards import require_project
from app.schemas import ActivityEventOut

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=list[ActivityEventOut])
def get_activity(project_id: int, since_id: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """
    The catch-up feed: everything that's happened in a project since
    since_id (claims, releases, expirations, invalidations, messages,
    registrations). A newly-starting or resuming agent reads this first to
    reconstruct context it doesn't otherwise have.
    """
    require_project(db, project_id)
    return (
        db.query(ActivityEvent)
        .filter(ActivityEvent.project_id == project_id, ActivityEvent.id > since_id)
        .order_by(ActivityEvent.id.asc())
        .limit(min(limit, 500))
        .all()
    )