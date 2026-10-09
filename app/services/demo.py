"""
A sample project the dashboard can load in one click, so its 3D views and
timeline have something to show before any real agent connects. It writes
the same rows real agents would produce (agents, file snapshots, claims,
messages, activity), back-dated over the last half hour.

It only ever deletes and recreates the project named in settings.demo_project.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    ActivityEvent,
    ActivityType,
    Agent,
    Claim,
    ClaimStatus,
    FileSnapshot,
    Message,
    Project,
)
from app.services.ast_conflict import resolve_range

FILES = {
    "app/models.py": '''from dataclasses import dataclass


@dataclass
class User:
    id: int
    name: str
    email: str

    def display(self):
        return f"{self.name} <{self.email}>"


@dataclass
class Order:
    id: int
    user_id: int
    items: list

    def count(self):
        return len(self.items)
''',
    "app/service.py": '''class UserService:
    def __init__(self, repo):
        self.repo = repo

    def create(self, name, email):
        user = {"name": name, "email": email}
        return self.repo.save(user)

    def delete(self, user_id):
        return self.repo.remove(user_id)


def helper(value):
    return value * 42
''',
    "app/pricing.py": '''TAX_RATE = 0.18


def subtotal(items):
    return sum(i["price"] * i["qty"] for i in items)


def total(items, coupon=None):
    amount = subtotal(items)
    if coupon:
        amount -= coupon.discount(amount)
    return round(amount * (1 + TAX_RATE), 2)
''',
    "web/checkout.tsx": '''export function Checkout({ cart }) {
  return <PriceSummary items={cart.items} />;
}

export function Cart() {
  return null;
}
''',
    "README.md": '''# storefront

A small shop used as Control Tower's sample project.

## Running
`make dev`
''',
}

AGENTS = [("claude-backend", "backend"), ("codex-frontend", "frontend"), ("gemini-tests", "tests")]


def _lines(src: str) -> int:
    return src.count("\n") + (0 if src.endswith("\n") else 1)


def seed_demo(db: Session) -> Project:
    now = datetime.now(timezone.utc)
    ago = lambda **kw: now - timedelta(**kw)  # noqa: E731

    old = db.query(Project).filter(Project.name == settings.demo_project).one_or_none()
    if old is not None:
        db.delete(old)
        db.flush()

    project = Project(name=settings.demo_project, repo_root="(sample project)", created_at=ago(minutes=40))
    db.add(project)
    db.flush()

    agents = {}
    for i, (name, role) in enumerate(AGENTS):
        a = Agent(project_id=project.id, name=name, role=role, created_at=ago(minutes=35 - i), last_seen_at=now)
        db.add(a)
        db.flush()
        agents[name] = a
    agents["codex-frontend"].last_seen_at = ago(seconds=70)

    for path, src in FILES.items():
        db.add(FileSnapshot(project_id=project.id, file_path=path, source=src, line_count=_lines(src),
                            updated_by=agents["claude-backend"].id, updated_at=ago(minutes=30)))

    events: list[tuple[datetime, ActivityType, Agent, dict]] = []
    for i, (name, role) in enumerate(AGENTS):
        events.append((ago(minutes=35 - i), ActivityType.agent_registered, agents[name], {"name": name, "role": role}))

    def add_claim(agent, path, symbol=None, lines=None, note=None, start=None, end=None, beat=None):
        ls, le = resolve_range(FILES[path], symbol, *(lines or (None, None)))
        c = Claim(project_id=project.id, agent_id=agent.id, file_path=path, symbol_name=symbol,
                  line_start=ls, line_end=le, note=note, created_at=start,
                  last_heartbeat_at=beat or end or start,
                  status=ClaimStatus.released if end else ClaimStatus.active,
                  released_at=end, release_note="done" if end else None)
        db.add(c)
        db.flush()
        payload = {"claim_id": c.id, "file_path": path, "symbol_name": symbol, "line_start": ls, "line_end": le, "note": note}
        events.append((start, ActivityType.claim_created, agent, payload))
        if end:
            events.append((end, ActivityType.claim_released, agent, {**payload, "note": "done"}))
        return c

    cb, cf, gt = agents["claude-backend"], agents["codex-frontend"], agents["gemini-tests"]
    add_claim(cb, "app/models.py", "Order.count", note="Count only active items", start=ago(minutes=27), end=ago(minutes=16))
    add_claim(gt, "app/pricing.py", "subtotal", note="Property tests", start=ago(minutes=21), end=ago(minutes=11))
    add_claim(cf, "README.md", lines=(1, 4), note="Document the checkout flow", start=ago(minutes=24), end=ago(minutes=3))
    add_claim(cf, "web/checkout.tsx", lines=(1, 3), note="Wire Checkout to the new pricing API",
              start=ago(minutes=12, seconds=40), beat=ago(minutes=8, seconds=50))
    create = add_claim(cb, "app/service.py", "UserService.create", note="Validate email and reject duplicates",
                       start=ago(minutes=4, seconds=12), beat=ago(seconds=40))
    add_claim(cb, "app/pricing.py", "total", note="Apply tax after the coupon, not before",
              start=ago(minutes=2), beat=ago(seconds=30))
    add_claim(gt, "app/service.py", "helper", note="Add unit tests", start=ago(seconds=63), beat=ago(seconds=20))

    events.append((ago(minutes=18), ActivityType.claim_rejected, gt,
                   {"file_path": "app/models.py", "symbol_name": "Order", "conflicting_claim_id": None, "conflicting_agent": "claude-backend"}))
    events.append((ago(seconds=90), ActivityType.claim_rejected, gt,
                   {"file_path": "app/service.py", "symbol_name": "UserService", "conflicting_claim_id": create.id, "conflicting_agent": "claude-backend"}))

    for at, frm, to, body in [
        (ago(seconds=80), gt, cb, "Need UserService for fixtures. Ping me when create() is done?"),
        (ago(seconds=70), cb, None, "About ten minutes. Grab helper() meanwhile, it needs tests too."),
    ]:
        m = Message(project_id=project.id, from_agent_id=frm.id, to_agent_id=to.id if to else None, body=body, created_at=at)
        db.add(m)
        db.flush()
        events.append((at, ActivityType.message_sent, frm, {"message_id": m.id, "to_agent_id": to.id if to else None, "preview": body[:120]}))

    # Insert in time order so event ids (the feed cursor) are chronological.
    for at, type_, a, payload in sorted(events, key=lambda e: e[0]):
        db.add(ActivityEvent(project_id=project.id, type=type_, agent_id=a.id, payload=payload, created_at=at))
    db.commit()
    db.refresh(project)
    return project