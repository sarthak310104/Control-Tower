"""
Authentication and authorisation.

Off by default: with CT_ADMIN_TOKEN unset every request is allowed, which is
how it runs on localhost. Set CT_ADMIN_TOKEN and every API call (except
/health and the dashboard's static files) needs `Authorization: Bearer <token>`:

- The admin token can do anything: create projects, issue join keys, act as
  any agent (the dashboard's Simulate panel), load the sample project.
- Each project has a join key. An agent presents it once to POST
  /agents/join and gets back its own agent token.
- An agent token can read its own project and act only as that agent: it
  can't claim, release, heartbeat or send messages as anyone else, and it
  can't see other projects.

Secrets (join keys, agent tokens) are random, shown once, and stored only as
SHA-256 hashes. The admin token is compared in constant time.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import Agent, AgentToken, Project


@dataclass(frozen=True)
class Principal:
    kind: str  # "open" (auth off), "admin" or "agent"
    agent_id: int | None = None
    project_id: int | None = None

    @property
    def unrestricted(self) -> bool:
        return self.kind in ("open", "admin")


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_join_key() -> str:
    return "ctj_" + secrets.token_urlsafe(24)


def new_agent_token() -> str:
    return "cta_" + secrets.token_urlsafe(32)


def issue_agent_token(db: Session, agent: Agent) -> str:
    token = new_agent_token()
    db.add(AgentToken(agent_id=agent.id, token_hash=hash_secret(token)))
    return token


def rotate_join_key(db: Session, project: Project) -> str:
    key = new_join_key()
    project.join_key_hash = hash_secret(key)
    return key


def join_key_matches(project: Project, key: str | None) -> bool:
    return bool(key) and project.join_key_hash is not None and hmac.compare_digest(project.join_key_hash, hash_secret(key))


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    settings = config.settings
    if not settings.auth_enabled:
        return Principal("open")
    token = _bearer(request)
    if token is None:
        raise HTTPException(401, "missing bearer token: send Authorization: Bearer <token>",
                            headers={"WWW-Authenticate": "Bearer"})
    if hmac.compare_digest(token.encode(), settings.admin_token.encode()):
        return Principal("admin")
    row = (
        db.query(AgentToken, Agent)
        .join(Agent, Agent.id == AgentToken.agent_id)
        .filter(AgentToken.token_hash == hash_secret(token), AgentToken.revoked_at.is_(None))
        .one_or_none()
    )
    if row is None:
        raise HTTPException(401, "invalid or revoked token", headers={"WWW-Authenticate": "Bearer"})
    tok, agent = row
    now = datetime.now(timezone.utc)
    if tok.last_used_at is None or now - tok.last_used_at > timedelta(minutes=1):  # don't write on every call
        tok.last_used_at = now
        db.commit()
    return Principal("agent", agent_id=agent.id, project_id=agent.project_id)


def require_admin(p: Principal) -> None:
    if not p.unrestricted:
        raise HTTPException(403, "this needs the admin token")


def require_project_access(p: Principal, project_id: int) -> None:
    if not p.unrestricted and p.project_id != project_id:
        raise HTTPException(403, "this token belongs to a different project")


def require_act_as(p: Principal, agent_id: int) -> None:
    if not p.unrestricted and p.agent_id != agent_id:
        raise HTTPException(403, "an agent token can only act as its own agent")