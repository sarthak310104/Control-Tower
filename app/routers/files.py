from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import Principal, get_principal, require_project_access
from app.db import get_db
from app.models import FileSnapshot
from app.routers._guards import require_project
from app.schemas import FileContentOut, FileOut, SymbolOut
from app.services.ast_conflict import UnparsableFileError, UnsupportedLanguageError, extract_symbols, language_for

router = APIRouter(prefix="/files", tags=["files"])


@router.get("", response_model=list[FileOut])
def list_files(project_id: int, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    """Every file an agent has sent content for in this project, with its line count."""
    require_project_access(p, project_id)
    require_project(db, project_id)
    return db.query(FileSnapshot).filter(FileSnapshot.project_id == project_id).order_by(FileSnapshot.file_path).all()


@router.get("/content", response_model=FileContentOut)
def file_content(project_id: int, file_path: str, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    """
    The latest content an agent sent for this file, plus its symbols when it's
    in a language with symbol support. This is a snapshot from the last claim,
    not the live file.
    """
    require_project_access(p, project_id)
    snap = (
        db.query(FileSnapshot)
        .filter(FileSnapshot.project_id == project_id, FileSnapshot.file_path == file_path)
        .one_or_none()
    )
    if snap is None:
        raise HTTPException(404, f"no snapshot of {file_path} yet; one is saved the first time an agent claims code in it")
    symbols = None
    if language_for(file_path) is not None:
        try:
            symbols = [SymbolOut(symbol=s.qualified_name, line_start=s.line_start, line_end=s.line_end)
                       for s in extract_symbols(snap.source, file_path).values()]
        except (UnparsableFileError, UnsupportedLanguageError):
            symbols = None
    return FileContentOut(file_path=snap.file_path, line_count=snap.line_count, updated_by=snap.updated_by,
                          updated_at=snap.updated_at, source=snap.source, symbols=symbols)