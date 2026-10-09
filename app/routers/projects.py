from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import Principal, get_principal, require_admin, require_project_access, rotate_join_key
from app.db import get_db
from app.models import Project
from app.schemas import JoinKeyOut, ProjectCreate, ProjectCreatedOut, ProjectOut

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectCreatedOut, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    """
    Idempotent by name. A new project gets a join key, returned only here
    (and from POST /projects/{id}/join-key); agents need it to join when
    auth is on. Needs the admin token when auth is on.
    """
    require_admin(p)
    existing = db.query(Project).filter(Project.name == payload.name).one_or_none()
    if existing is not None:
        return existing
    project = Project(name=payload.name, repo_root=payload.repo_root)
    key = rotate_join_key(db, project)
    db.add(project)
    db.commit()
    db.refresh(project)
    return ProjectCreatedOut.model_validate(project).model_copy(update={"join_key": key})


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    q = db.query(Project)
    if not p.unrestricted:
        q = q.filter(Project.id == p.project_id)
    return q.order_by(Project.created_at.desc()).all()


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    require_project_access(p, project_id)
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


@router.post("/{project_id}/join-key", response_model=JoinKeyOut)
def new_join_key(project_id: int, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    """Issues a fresh join key and invalidates the old one. Agents already joined keep their tokens."""
    require_admin(p)
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    key = rotate_join_key(db, project)
    db.commit()
    return JoinKeyOut(project_id=project.id, join_key=key)