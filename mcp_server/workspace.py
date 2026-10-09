"""
Local-filesystem side of the MCP adapter: turning whatever path an agent
passes into one canonical project-relative path, and reading the file's
current content to send to the coordinator.

Canonical paths matter more than they look. The coordinator compares
file_path strings exactly, so if one agent claims "app\\main.py" (Windows),
another "./app/main.py" and a third "/home/me/repo/app/main.py", they must
all collapse to "app/main.py" or the coordinator would see three different
files and let all three agents edit the same function.
"""

from pathlib import Path, PurePosixPath


class WorkspaceError(Exception):
    pass


# Generous but bounded: AST-parsing a multi-megabyte generated file on every
# conflict check is not something an agent should be doing by accident.
MAX_FILE_BYTES = 2_000_000


def canonical_path(repo_root: Path, file_path: str) -> str:
    raw = file_path.strip().replace("\\", "/")
    if not raw:
        raise WorkspaceError("file_path is empty")
    candidate = Path(raw)
    absolute = (candidate if candidate.is_absolute() else repo_root / candidate).resolve()
    try:
        relative = absolute.relative_to(repo_root)
    except ValueError:
        raise WorkspaceError(f"{file_path!r} is outside the repo root {repo_root}")
    if str(relative) in ("", "."):
        raise WorkspaceError(f"{file_path!r} is the repo root itself, not a file")
    return PurePosixPath(*relative.parts).as_posix()


def read_source(repo_root: Path, rel_path: str) -> str:
    path = repo_root / rel_path
    if not path.is_file():
        raise WorkspaceError(
            f"{rel_path} does not exist in {repo_root}. To claim work on a file you're about to "
            f"create, create it first (even empty), then claim a line range or symbol in it."
        )
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise WorkspaceError(f"{rel_path} is {size} bytes, over the {MAX_FILE_BYTES}-byte limit")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise WorkspaceError(f"{rel_path} is not UTF-8 text; only text files can be claimed")