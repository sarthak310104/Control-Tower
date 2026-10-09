import threading
import time
from datetime import datetime, timedelta, timezone

from app.models import Claim, ClaimStatus
from app.services.claims import reap_expired_claims
from conftest import SOURCE, claim_body


def test_conflict_on_same_symbol_and_free_on_sibling(client, project, agents):
    alice, bob = agents
    assert client.post("/claims", json=claim_body(project, alice, "UserService.create")).status_code == 201

    r = client.post("/claims", json=claim_body(project, bob, "UserService.create"))
    assert r.status_code == 409
    assert r.json()["detail"]["conflicting_agent"] == "alice"

    assert client.post("/claims", json=claim_body(project, bob, "UserService.delete")).status_code == 201


def test_class_claim_conflicts_with_method_claim(client, project, agents):
    alice, bob = agents
    client.post("/claims", json=claim_body(project, alice, "UserService.delete"))
    assert client.post("/claims", json=claim_body(project, bob, "UserService")).status_code == 409


def test_own_claims_never_conflict(client, project, agents):
    alice, _ = agents
    assert client.post("/claims", json=claim_body(project, alice, "UserService")).status_code == 201
    assert client.post("/claims", json=claim_body(project, alice, "UserService.create")).status_code == 201


def test_conflict_check_uses_current_lines_not_snapshot(client, project, agents):
    """Alice claims helper at lines 9-10; code is inserted above it; Bob's range claim on the
    OLD lines must now be free, and on the NEW lines must conflict."""
    alice, bob = agents
    client.post("/claims", json=claim_body(project, alice, "helper"))
    shifted = "\n" * 20 + SOURCE
    assert client.post("/claims", json=claim_body(project, bob, source=shifted, lines=(9, 10))).status_code == 201
    assert client.post("/claims", json=claim_body(project, bob, source=shifted, lines=(29, 30))).status_code == 409


def test_dry_run_reports_conflicts_and_writes_nothing(client, project, agents):
    alice, bob = agents
    client.post("/claims", json=claim_body(project, alice, "helper", note="tuning"))
    before = client.get("/activity", params={"project_id": project["id"]}).json()

    r = client.post("/claims/check", json=claim_body(project, bob, "helper"))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["conflicts"][0]["agent_name"] == "alice"
    assert body["conflicts"][0]["note"] == "tuning"

    after = client.get("/activity", params={"project_id": project["id"]}).json()
    assert after == before
    assert len(client.get("/claims", params={"project_id": project["id"]}).json()) == 1


def test_deleted_symbol_claim_is_invalidated(client, project, agents):
    alice, bob = agents
    first = client.post("/claims", json=claim_body(project, alice, "helper")).json()
    without_helper = SOURCE.split("def helper")[0]
    assert client.post("/claims", json=claim_body(project, bob, "UserService", source=without_helper)).status_code == 201

    claims = {c["id"]: c for c in client.get("/claims", params={"project_id": project["id"]}).json()}
    assert claims[first["id"]]["status"] == "invalidated"


def test_unparsable_file_does_not_invalidate_symbol_claims(client, project, agents):
    """Regression: a mid-edit (invalid) file used to be treated like 'symbol deleted',
    silently wiping every symbol claim on the file."""
    alice, bob = agents
    first = client.post("/claims", json=claim_body(project, alice, "helper")).json()
    broken = SOURCE + "\ndef half_written(:\n"

    # Bob's range covers helper's last known lines: refused, using the snapshot as a fallback.
    assert client.post("/claims", json=claim_body(project, bob, source=broken, lines=(9, 9))).status_code == 409
    # A range elsewhere is fine, and alice's claim stays active.
    assert client.post("/claims", json=claim_body(project, bob, source=broken, lines=(1, 3))).status_code == 201
    claims = {c["id"]: c for c in client.get("/claims", params={"project_id": project["id"]}).json()}
    assert claims[first["id"]]["status"] == "active"


def test_symbol_claim_on_unparsable_file_is_422(client, project, agents):
    alice, _ = agents
    r = client.post("/claims", json=claim_body(project, alice, "helper", source="def x(:\n"))
    assert r.status_code == 422 and "doesn't parse" in r.json()["detail"]


def test_concurrent_claims_on_same_symbol_grant_exactly_one(client, project):
    """Regression for the check-then-insert race: N agents claim the same function at once."""
    n = 12
    racers = [client.post("/agents", json={"project_id": project["id"], "name": f"racer-{i}"}).json() for i in range(n)]
    barrier = threading.Barrier(n)
    codes = []

    def go(agent):
        barrier.wait()
        codes.append(client.post("/claims", json=claim_body(project, agent, "helper")).status_code)

    threads = [threading.Thread(target=go, args=(a,)) for a in racers]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [201] + [409] * (n - 1)


def test_timestamp_defaults_are_per_row(client, project, agents, db):
    """Regression: server_default='now()' was a string literal that Postgres froze at CREATE TABLE
    time, so every claim looked as old as the table and the reaper killed fresh claims."""
    alice, bob = agents
    a = client.post("/claims", json=claim_body(project, alice, "helper")).json()
    time.sleep(0.05)
    b = client.post("/claims", json=claim_body(project, bob, "UserService.create")).json()
    assert b["last_heartbeat_at"] > a["last_heartbeat_at"]
    age = datetime.now(timezone.utc) - datetime.fromisoformat(b["created_at"])
    assert age < timedelta(seconds=30)


def test_reaper_expires_only_stale_claims(client, project, agents, db):
    alice, bob = agents
    stale = client.post("/claims", json=claim_body(project, alice, "helper")).json()
    fresh = client.post("/claims", json=claim_body(project, bob, "UserService.create")).json()
    db.query(Claim).filter(Claim.id == stale["id"]).update(
        {Claim.last_heartbeat_at: datetime.now(timezone.utc) - timedelta(hours=1)}
    )
    db.commit()

    assert [c.id for c in reap_expired_claims(db)] == [stale["id"]]
    statuses = {c["id"]: c["status"] for c in client.get("/claims", params={"project_id": project["id"]}).json()}
    assert statuses == {stale["id"]: "expired", fresh["id"]: "active"}
    types = [e["type"] for e in client.get("/activity", params={"project_id": project["id"]}).json()]
    assert "claim_expired" in types


def test_agent_heartbeat_refreshes_all_held_claims(client, project, agents, db):
    alice, _ = agents
    for sym in ("helper", "UserService.create"):
        client.post("/claims", json=claim_body(project, alice, sym))
    db.query(Claim).update({Claim.last_heartbeat_at: datetime.now(timezone.utc) - timedelta(hours=1)})
    db.commit()

    r = client.post("/claims/heartbeat", params={"agent_id": alice["id"]})
    assert r.json()["claims_refreshed"] == 2
    assert reap_expired_claims(db) == []


def test_release_rules(client, project, agents):
    alice, bob = agents
    c = client.post("/claims", json=claim_body(project, alice, "helper")).json()
    assert client.post(f"/claims/{c['id']}/release", json={"agent_id": bob["id"]}).status_code == 409
    assert client.post(f"/claims/{c['id']}/release", json={"agent_id": alice["id"], "release_note": "done"}).status_code == 200
    assert client.post(f"/claims/{c['id']}/release", json={"agent_id": alice["id"]}).status_code == 409
    assert client.post("/claims/9999/release", json={"agent_id": alice["id"]}).status_code == 404
    # released code is claimable again
    assert client.post("/claims", json=claim_body(project, bob, "helper")).status_code == 201


def test_unknown_or_foreign_agent_is_rejected_cleanly(client, project, agents):
    alice, _ = agents
    other = client.post("/projects", json={"name": "other"}).json()
    assert client.post("/claims", json=claim_body(project, {"id": 999}, "helper")).status_code == 404
    assert client.post("/claims", json=claim_body(other, alice, "helper")).status_code == 422
    assert client.post("/agents", json={"project_id": 999, "name": "x"}).status_code == 404


def test_inbox_direct_and_broadcast_excluding_own(client, project, agents):
    alice, bob = agents
    send = lambda frm, to, body: client.post(  # noqa: E731
        "/messages", json={"project_id": project["id"], "from_agent_id": frm["id"], "to_agent_id": to, "body": body}
    )
    send(alice, None, "broadcast from alice")
    send(alice, bob["id"], "direct to bob")
    send(bob, alice["id"], "direct to alice")

    inbox = lambda who, since=0: client.get(  # noqa: E731
        "/messages/inbox", params={"project_id": project["id"], "agent_id": who["id"], "since_id": since}
    ).json()
    assert [m["body"] for m in inbox(bob)] == ["broadcast from alice", "direct to bob"]
    assert [m["body"] for m in inbox(alice)] == ["direct to alice"]
    assert inbox(bob, since=inbox(bob)[-1]["id"]) == []
    assert len(client.get("/messages", params={"project_id": project["id"]}).json()) == 3
    assert send(alice, None, "   ").status_code == 422


def test_agent_registration_is_idempotent(client, project):
    a = client.post("/agents", json={"project_id": project["id"], "name": "same", "role": "backend"}).json()
    b = client.post("/agents", json={"project_id": project["id"], "name": "same"}).json()
    assert a["id"] == b["id"] and b["role"] == "backend"
    assert len(client.get("/agents", params={"project_id": project["id"]}).json()) == 1


def test_symbols_endpoint(client):
    rows = client.post("/claims/symbols", json={"source": SOURCE}).json()
    assert {r["symbol"]: (r["line_start"], r["line_end"]) for r in rows}["UserService.create"] == (2, 3)
    assert client.post("/claims/symbols", json={"source": "def x(:\n"}).status_code == 422


def test_health_exposes_ttl_and_dashboard_is_served(client):
    assert client.get("/health").json()["claim_ttl_seconds"] > 0
    page = client.get("/ui/")
    assert page.status_code == 200 and "Control Tower" in page.text
    assert client.get("/", follow_redirects=False).headers["location"] == "/ui/"


def test_claims_record_file_snapshots(client, project, agents):
    alice, _ = agents
    client.post("/claims", json=claim_body(project, alice, "helper"))
    files = client.get("/files", params={"project_id": project["id"]}).json()
    assert [(f["file_path"], f["line_count"]) for f in files] == [("app/service.py", 10)]
    content = client.get("/files/content", params={"project_id": project["id"], "file_path": "app/service.py"}).json()
    assert content["source"] == SOURCE
    assert {s["symbol"] for s in content["symbols"]} >= {"UserService.create", "helper"}
    assert client.get("/files/content", params={"project_id": project["id"], "file_path": "nope.py"}).status_code == 404


def test_snapshot_follows_latest_content(client, project, agents):
    alice, bob = agents
    client.post("/claims", json=claim_body(project, alice, "helper"))
    client.post("/claims", json=claim_body(project, bob, "UserService.create", source="\n" + SOURCE))
    files = client.get("/files", params={"project_id": project["id"]}).json()
    assert files[0]["line_count"] == 11 and files[0]["updated_by"] == bob["id"]


def test_demo_seed_is_repeatable_and_scoped(client, project):
    first = client.post("/demo/seed").json()
    second = client.post("/demo/seed").json()
    assert first["name"] == second["name"] == "storefront-demo"
    pid = second["id"]
    assert len(client.get("/agents", params={"project_id": pid}).json()) == 3
    assert len(client.get("/claims", params={"project_id": pid, "status": "active"}).json()) == 4
    assert len(client.get("/files", params={"project_id": pid}).json()) == 5
    types = [e["type"] for e in client.get("/activity", params={"project_id": pid}).json()]
    assert types.count("claim_rejected") == 2
    assert client.get("/projects/" + str(project["id"])).status_code == 200  # other projects untouched


TS_SOURCE = """\
export class CartStore {
  add(item: string) {
    return item;
  }
  clear() {
    return [];
  }
}
"""


def test_typescript_symbol_claims_conflict_like_python(client, project, agents):
    alice, bob = agents
    body = lambda who, sym: claim_body(project, who, sym, source=TS_SOURCE, file_path="web/cart.ts")  # noqa: E731
    assert client.post("/claims", json=body(alice, "CartStore.add")).status_code == 201
    assert client.post("/claims", json=body(bob, "CartStore")).status_code == 409        # class contains the method
    assert client.post("/claims", json=body(bob, "CartStore.clear")).status_code == 201  # sibling method is free
    symbols = client.get("/files/content", params={"project_id": project["id"], "file_path": "web/cart.ts"}).json()["symbols"]
    assert {s["symbol"] for s in symbols} == {"CartStore", "CartStore.add", "CartStore.clear"}


def test_symbol_claim_in_unsupported_language_is_a_clear_422(client, project, agents):
    alice, _ = agents
    r = client.post("/claims", json=claim_body(project, alice, "main", source="puts 1\n", file_path="bin/run.rb"))
    assert r.status_code == 422 and "line range" in r.json()["detail"]
    ok = client.post("/claims", json=claim_body(project, alice, source="puts 1\n", file_path="bin/run.rb", lines=(1, 1)))
    assert ok.status_code == 201


def test_symbols_endpoint_uses_the_file_extension(client):
    rows = client.post("/claims/symbols", json={"source": "func helper() int {\n\treturn 1\n}\n", "file_path": "x.go"}).json()
    assert rows == [{"symbol": "helper", "line_start": 1, "line_end": 3}]


def test_reaper_expires_claims_held_too_long_even_with_heartbeats(client, project, agents, db):
    alice, _ = agents
    c = client.post("/claims", json=claim_body(project, alice, "helper")).json()
    db.query(Claim).filter(Claim.id == c["id"]).update({Claim.created_at: datetime.now(timezone.utc) - timedelta(hours=5)})
    db.commit()
    client.post("/claims/heartbeat", params={"agent_id": alice["id"]})  # still heartbeating
    assert reap_expired_claims(db, max_age_seconds=4 * 3600)[0].id == c["id"]
    expired = [e for e in client.get("/activity", params={"project_id": project["id"]}).json() if e["type"] == "claim_expired"]
    assert expired[0]["payload"]["reason"] == "max_age"
    assert reap_expired_claims(db, max_age_seconds=0) == []  # 0 turns the limit off


def test_oversized_and_malformed_input_is_rejected(client, project, agents):
    alice, _ = agents
    assert client.post("/projects", json={"name": "has spaces"}).status_code == 422
    body = claim_body(project, alice, "helper", note="x" * 5000)
    assert client.post("/claims", json=body).status_code == 422