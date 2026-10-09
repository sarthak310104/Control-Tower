"""
What an agent actually changed, from git, in the shape POST /reconcile wants.

Runs locally, next to the agent's checkout (the coordinator never touches
anyone's disk). Compares the working tree against the commit that was HEAD
when the session started, so commits the agent made during the session count
too, not just uncommitted edits. Untracked files are reported as added.

`git diff -U0` gives hunk headers like `@@ -9,2 +9,3 @@`: the `+9,3` part
is the changed lines in the current file (9-11). A pure deletion (`+8,0`)
leaves no lines behind, so it's pinned to the line it happened after.
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's well-known empty tree object
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
MAX_FILES = 300


class GitUnavailable(Exception):
    pass


@dataclass
class Change:
    path: str
    status: str = "modified"  # modified | added | deleted
    ranges: list[tuple[int, int]] = field(default_factory=list)


def _git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-c", "core.quotepath=off", "-C", str(root), *args],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise GitUnavailable(f"git isn't available here ({exc.__class__.__name__})") from exc
    if out.returncode != 0:
        raise GitUnavailable(out.stderr.strip() or f"git {args[0]} failed")
    return out.stdout


def head(root: Path) -> str | None:
    """The current HEAD commit, None if the repo has no commits yet. Raises GitUnavailable outside a repo."""
    _git(root, "rev-parse", "--git-dir")
    try:
        return _git(root, "rev-parse", "--verify", "-q", "HEAD").strip() or None
    except GitUnavailable:
        return None


def _strip(path: str) -> str:
    return path[2:] if path[:2] in ("a/", "b/") else path


def parse_diff(text: str) -> list[Change]:
    changes: list[Change] = []
    cur: Change | None = None
    old_path = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            cur, old_path = None, None
        elif line.startswith("--- "):
            old_path = None if line[4:] == "/dev/null" else _strip(line[4:])
        elif line.startswith("+++ "):
            new = line[4:]
            if new == "/dev/null":
                cur = Change(old_path or "", status="deleted")
            else:
                cur = Change(_strip(new), status="added" if old_path is None else "modified")
            changes.append(cur)
        elif cur is not None and line.startswith("@@"):
            m = HUNK.match(line)
            if not m or cur.status == "deleted":
                continue
            start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            if count > 0:
                cur.ranges.append((start, start + count - 1))
            else:
                cur.ranges.append((max(start, 1), max(start, 1)))
    return [c for c in changes if c.path]


def collect(root: Path, base: str | None, only: set[str] | None = None) -> list[Change]:
    """Every change in the working tree (below root) since `base`, plus untracked files."""
    diff = _git(root, "diff", "--no-color", "--no-ext-diff", "--relative", "-U0", "--find-renames", base or EMPTY_TREE, "--")
    changes = parse_diff(diff)
    for path in _git(root, "ls-files", "--others", "--exclude-standard").splitlines():
        if path:
            changes.append(Change(path, status="added"))
    if only is not None:
        changes = [c for c in changes if c.path in only]
    return changes[:MAX_FILES]