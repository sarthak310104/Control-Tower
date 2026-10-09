"""Auth on: admin token, join keys, per-agent tokens, and what each may do."""

from sqlalchemy import text

from conftest import SOURCE, claim_body


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def setup_project(client, admin, name="secure"):
    r = client.post("/projects", json={"name": name}, headers=admin)
    assert r.status_code == 201
    return r.json()


def join(client, project_name, key, name):
    return client.post("/agents/join", json={"project": project_name, "name": name}, headers={"X-Join-Key": key})


def test_requests_need_a_token(client, auth_on):
    assert client.get("/projects").status_code == 401
    assert client.get("/projects", headers=bearer("nope")).status_code == 401
    assert client.get("/projects", headers=auth_on).status_code == 200
    health = client.get("/health").json()
    assert health["auth_enabled"] is True  # /health stays public


def test_agents_join_with_the_project_key_and_get_their_own_token(client, auth_on):
    p = setup_project(client, auth_on)
    assert p["join_key"].startswith("ctj_")
    assert join(client, "secure", "wrong", "alice").status_code == 403
    assert join(client, "missing-project", p["join_key"], "alice").status_code == 404
    r = join(client, "secure", p["join_key"], "alice")
    assert r.status_code == 200 and r.json()["token"].startswith("cta_")
    assert r.json()["project_id"] == p["id"]


def test_agent_token_acts_only_as_itself_in_its_own_project(client, auth_on):
    p = setup_project(client, auth_on)
    other = setup_project(client, auth_on, "other")
    alice = join(client, "secure", p["join_key"], "alice").json()
    bob = join(client, "secure", p["join_key"], "bob").json()
    a_tok, a = bearer(alice["token"]), alice["agent"]
    b = bob["agent"]

    ok = client.post("/claims", json=claim_body(p, a, "helper"), headers=a_tok)
    assert ok.status_code == 201
    assert client.post("/claims", json=claim_body(p, b, "UserService"), headers=a_tok).status_code == 403
    assert client.post(f"/claims/{ok.json()['id']}/release", json={"agent_id": b["id"]}, headers=a_tok).status_code == 403
    assert client.post("/messages", json={"project_id": p["id"], "from_agent_id": b["id"], "body": "hi"}, headers=a_tok).status_code == 403
    assert client.get("/messages/inbox", params={"project_id": p["id"], "agent_id": b["id"]}, headers=a_tok).status_code == 403

    assert client.get("/claims", params={"project_id": p["id"]}, headers=a_tok).status_code == 200
    assert client.get("/claims", params={"project_id": other["id"]}, headers=a_tok).status_code == 403
    assert [x["name"] for x in client.get("/projects", headers=a_tok).json()] == ["secure"]


def test_admin_only_operations(client, auth_on):
    p = setup_project(client, auth_on)
    a_tok = bearer(join(client, "secure", p["join_key"], "alice").json()["token"])
    assert client.post("/projects", json={"name": "x"}, headers=a_tok).status_code == 403
    assert client.post("/agents", json={"project_id": p["id"], "name": "mallory"}, headers=a_tok).status_code == 403
    assert client.post("/demo/seed", headers=a_tok).status_code == 403
    assert client.post(f"/projects/{p['id']}/join-key", headers=a_tok).status_code == 403
    # the admin can act as any agent (the dashboard's Simulate panel)
    bob = client.post("/agents", json={"project_id": p["id"], "name": "bob"}, headers=auth_on).json()
    assert client.post("/claims", json=claim_body(p, bob, "helper"), headers=auth_on).status_code == 201


def test_rotating_the_join_key_and_revoking_tokens(client, auth_on):
    p = setup_project(client, auth_on)
    alice = join(client, "secure", p["join_key"], "alice").json()
    new_key = client.post(f"/projects/{p['id']}/join-key", headers=auth_on).json()["join_key"]
    assert join(client, "secure", p["join_key"], "bob").status_code == 403   # old key no longer joins
    assert join(client, "secure", new_key, "bob").status_code == 200
    a_tok = bearer(alice["token"])
    assert client.get("/agents", params={"project_id": p["id"]}, headers=a_tok).status_code == 200  # joined agents keep working
    assert client.delete(f"/agents/{alice['agent']['id']}/tokens", headers=a_tok).json()["revoked"] == 1
    assert client.get("/agents", params={"project_id": p["id"]}, headers=a_tok).status_code == 401


def test_secrets_are_stored_hashed(client, auth_on, db):
    p = setup_project(client, auth_on)
    token = join(client, "secure", p["join_key"], "alice").json()["token"]
    dump = " ".join(str(v) for row in db.execute(text("SELECT * FROM agent_tokens")).all() for v in row)
    dump += " ".join(str(v) for row in db.execute(text("SELECT * FROM projects")).all() for v in row)
    assert token not in dump and p["join_key"] not in dump


def test_auth_off_keeps_the_old_open_behaviour(client):
    r = client.post("/agents/join", json={"project": "open-proj", "name": "alice"})
    assert r.status_code == 200 and r.json()["token"] is None  # project created on join
    pid = r.json()["project_id"]
    agent = r.json()["agent"]
    body = claim_body({"id": pid}, agent, "helper")
    assert client.post("/claims", json=body).status_code == 201