from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import Principal, get_principal, require_admin
from app.config import settings
from app.db import get_db
from app.schemas import ProjectOut
from app.services.demo import seed_demo

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("/seed", response_model=ProjectOut)
def seed(db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    """(Re)creates the sample project so the dashboard has something to show. Touches nothing else."""
    require_admin(p)
    if not settings.demo_enabled:
        raise HTTPException(404, "the sample project is turned off on this coordinator (DEMO_ENABLED=false)")
    return seed_demo(db)