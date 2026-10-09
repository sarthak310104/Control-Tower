"""Git reconciliation: changed lines vs claims."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Claim
from app.services.reconcile import merge, subtract
from conftest import SOURCE, claim_body

# SOURCE (conftest) layout: UserService 1-6 (create 2-3, delete 5-6), helper 9-10


def reconcile(client, project, agent, changed, source=SOURCE, path="app/service.py", status="modified", base="abc123"):
    return client.post("/reconcile", json={
        "project_id": project["id"], "agent_id": agent["id"], "base": base,
        "files": [{"file_path": path, "source": source, "changed": changed, "status": status}],
    })


def events(client, project, *types):
    return [e for e in client.get("/activity", params={"project_id": project["id"]}).json() if e["type"] in types]


@pytest.mark.parametrize("ranges,expected", [
    ([(5, 9), (1, 3), (4, 4)], [(1, 9)]),
    ([(1, 2), (4, 5)], [(1, 2), (4, 5)]),
    ([(3, 1)], [(1, 3)]),
])
def test_merge(ranges, expected):
    assert merge(ranges) == expected


@pytest.mark.parametrize("ranges,cover,expected", [
    ([(1, 10)], [(3, 4), (8, 20)], [(1, 2), (5, 7)]),
    ([(1, 3)], [(1, 3)], []),
    ([(5, 6)], [], [(5, 6)]),
    ([(1, 2), (9, 10)], [(2, 9)], [(1, 1), (10, 10)]),
])
def test_subtract(ranges, cover, expected):
    assert subtract(ranges, cover) == expected


def test_edits_inside_own_claim_are_clean(client, project, agents):
    alice, _ = agents
    client.post("/claims", json=claim_body(project, alice, "UserService.create"))
    r = reconcile(client, project, alice, [(2, 3)]).json()
    assert r["clean"] and r["lines_changed"] == r["lines_covered"] == 2
    assert events(client, project, "edit_unclaimed", "edit_conflict") == []


def test_unclaimed_edit_is_flagged_with_the_symbols_it_touched(client, project, agents):
    alice, _ = agents
    client.post("/claims", json=claim_body(project, alice, "UserService.create"))
    r = reconcile(client, project, alice, [(2, 3), (9, 10)]).json()
    assert not r["clean"] and r["lines_covered"] == 2
    [f] = r["findings"]
    assert f["kind"] == "edit_unclaimed" and f["ranges"] == [[9, 10]] and f["symbols"] == ["helper"] and f["new"]
    [ev] = events(client, project, "edit_unclaimed")
    assert ev["payload"]["symbols"] == ["helper"] and ev["payload"]["base"] == "abc123"


def test_editing_someone_elses_claim_is_a_conflict(client, project, agents):
    alice, bob = agents
    client.post("/claims", json=claim_body(project, bob, "helper", note="tests"))
    [f] = reconcile(client, project, alice, [(10, 10)]).json()["findings"]
    assert f["kind"] == "edit_conflict"
    assert f["overlaps"][0]["agent"] == "bob" and f["overlaps"][0]["target"] == "helper"
    assert len(events(client, project, "edit_conflict")) == 1


def test_claims_follow_names_when_lines_shift(client, project, agents):
    alice, _ = agents
    client.post("/claims", json=claim_body(project, alice, "helper"))  # lines 9-10 in SOURCE
    shifted = "import os\nimport sys\n\n" + SOURCE  # helper is now 12-13
    assert reconcile(client, project, alice, [(12, 13)], source=shifted).json()["clean"]


def test_recently_released_claims_still_cover_but_old_ones_dont(client, project, agents, db):
    alice, _ = agents
    c = client.post("/claims", json=claim_body(project, alice, "helper")).json()
    client.post(f"/claims/{c['id']}/release", json={"agent_id": alice["id"]})
    assert reconcile(client, project, alice, [(9, 10)]).json()["clean"]
    db.query(Claim).filter(Claim.id == c["id"]).update({Claim.released_at: datetime.now(timezone.utc) - timedelta(hours=3)})
    db.commit()
    assert not reconcile(client, project, alice, [(9, 10)]).json()["clean"]


def test_reconciling_twice_reports_once(client, project, agents):
    alice, _ = agents
    first = reconcile(client, project, alice, [(9, 10)]).json()["findings"][0]
    second = reconcile(client, project, alice, [(9, 10)]).json()["findings"][0]
    assert first["new"] and not second["new"]
    assert len(events(client, project, "edit_unclaimed")) == 1


def test_new_and_deleted_files_are_listed_not_flagged(client, project, agents):
    alice, _ = agents
    body = {"project_id": project["id"], "agent_id": alice["id"], "files": [
        {"file_path": "app/new.py", "source": "x = 1\n", "changed": [(1, 1)], "status": "added"},
        {"file_path": "app/old.py", "source": None, "status": "deleted"},
    ]}
    r = client.post("/reconcile", json=body).json()
    assert r["clean"] and r["added"] == ["app/new.py"] and r["deleted"] == ["app/old.py"]


def test_reconcile_refreshes_snapshots_of_tracked_files(client, project, agents):
    alice, _ = agents
    client.post("/claims", json=claim_body(project, alice, "helper"))
    edited = SOURCE.replace("return 42", "return 43")
    reconcile(client, project, alice, [(10, 10)], source=edited)
    content = client.get("/files/content", params={"project_id": project["id"], "file_path": "app/service.py"}).json()
    assert "return 43" in content["source"]