"""The wizard with accounts from the central users service (config.AUTH == "central"), against ebad_users.testing.FakeUsers:
the host passes the user's `icm` membership in; admins run the users menu through the service."""
import os, sys, tempfile
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "users"))
os.environ["ICM_OPEN"] = "0"
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import ebad_users  # noqa: F401
except ImportError:
    print("fastapi / ebad_users not installed: skipped"); sys.exit(0)
from icm import central, config, project
from icm.api import make_router
from ebad_users.testing import FakeUsers

X = {"X-ICM": "1"}


def world(monkeypatch):
    d = tempfile.mkdtemp(prefix="icmcentral_")
    monkeypatch.setattr(config, "CACHE_DIR", d)
    monkeypatch.setattr(config, "JOBS_DB", os.path.join(d, "jobs.sqlite"))
    monkeypatch.setattr(config, "AUTH", "central")
    monkeypatch.setattr(config, "OPEN", False)
    monkeypatch.setenv("ICM_SIGNUP_KEY", "KEY1")
    fake = FakeUsers()
    fake.add_app("icm", roles=["admin", "editor", "viewer"], admin_roles=["admin"])
    fake.add_user("root", display_name="Root", memberships={"icm": {"roles": ["admin"]}})
    fake.add_user("alice", display_name="Alice", memberships={"icm": {"roles": ["editor"]}})
    fake.add_user("vera", display_name="Vera", memberships={"icm": {"roles": ["viewer"]}})
    fake.add_user("newbie", display_name="Newbie", memberships={"icm": {"roles": [], "status": "pending"}})
    fake.add_user("outsider", display_name="Out")
    users = fake.client(apps=("icm",))
    monkeypatch.setattr(central, "_client", users)
    monkeypatch.setattr(central, "_pending_memo", (0.0, 0))

    def host_user(request):
        cookie = request.cookies.get("ebad_sso")
        state = users.compat_user(cookie) if cookie else None
        return central.user_from_state(state)

    a = FastAPI()
    a.include_router(make_router(host_user))
    return fake, a


def as_user(fake, a, name=None):
    c = TestClient(a, follow_redirects=False)
    if name:
        c.cookies.set("ebad_sso", fake.login(name))
    return c


def test_me_for_every_kind_of_visitor(monkeypatch):
    fake, a = world(monkeypatch)
    anon = as_user(fake, a).get("/icm/api/me").json()
    assert anon["user"] is None and anon["accounts"] and anon["central"]["login"].startswith("https://ebadollah.ir/user/login")
    assert anon["central"]["signup"] == "https://ebadollah.ir/user/signup?app=icm&k=KEY1&next=/icm"
    root = as_user(fake, a, "root").get("/icm/api/me").json()
    assert root["user"] == "root" and root["admin"] and root["role"] == "admin" and root["pending"] == 1 and root["quota"] == 20
    alice = as_user(fake, a, "alice").get("/icm/api/me").json()
    assert alice["user"] == "alice" and not alice["admin"] and alice["role"] == "editor" and "pending" not in alice
    new = as_user(fake, a, "newbie").get("/icm/api/me").json()
    assert new["user"] is None and new["membership"] == "pending"
    out = as_user(fake, a, "outsider").get("/icm/api/me").json()
    assert out["user"] is None and "membership" not in out


def test_login_pages_lead_to_the_central_service(monkeypatch):
    fake, a = world(monkeypatch)
    c = as_user(fake, a)
    assert c.get("/icm/login?next=/icm/new").headers["location"] == "https://ebadollah.ir/user/login?next=/icm/new"
    assert c.get("/icm/login?next=//evil.example").headers["location"] == "https://ebadollah.ir/user/login?next=/icm"
    assert c.get("/icm/register").headers["location"] == "https://ebadollah.ir/user/signup?app=icm&k=KEY1&next=/icm"
    assert c.get("/icm/account").headers["location"] == "https://ebadollah.ir/user/account"
    assert as_user(fake, a, "alice").get("/icm/login?next=/icm/new").headers["location"] == "/icm/new"
    assert c.get("/icm/users").status_code == 200                    # the SPA page itself


def test_users_menu_through_the_service(monkeypatch):
    fake, a = world(monkeypatch)
    root, alice = as_user(fake, a, "root"), as_user(fake, a, "alice")
    assert alice.get("/icm/api/users").status_code == 403 and as_user(fake, a).get("/icm/api/users").status_code == 401
    project.create("v02:1", "alice")
    body = root.get("/icm/api/users").json()
    assert body["roles"] == ["admin", "editor", "viewer"] and body["default_quota"] == 20
    names = [u["username"] for u in body["users"]]
    assert names[0] == "newbie" and set(names) == {"root", "alice", "vera", "newbie"}
    al = next(u for u in body["users"] if u["username"] == "alice")
    assert al["role"] == "editor" and al["status"] == "active" and al["projects"] == 1 and al["open_projects"] == 1 and al["name"] == "Alice"
    assert root.post("/icm/api/users/newbie", json={"action": "approve"}).status_code == 403                  # X-ICM
    assert root.post("/icm/api/users/newbie", json={"action": "approve"}, headers=X).json() == {"ok": True}
    assert fake.membership("newbie", "icm")["status"] == "active"
    assert root.post("/icm/api/users/newbie", json={"action": "approve"}, headers=X).status_code == 409
    assert root.get("/icm/api/me").json()["pending"] == 0
    assert root.post("/icm/api/users/alice", json={"action": "role", "value": "viewer"}, headers=X).status_code == 200
    assert fake.membership("alice", "icm")["roles"] == ["viewer"]
    assert root.post("/icm/api/users/alice", json={"action": "role", "value": "god"}, headers=X).status_code == 400
    assert root.post("/icm/api/users/alice", json={"action": "quota", "value": 3}, headers=X).status_code == 200
    assert fake.membership("alice", "icm")["data"]["quota"] == 3
    for bad in (-1, "3", 1001, True):
        assert root.post("/icm/api/users/alice", json={"action": "quota", "value": bad}, headers=X).status_code == 400, bad
    root.post("/icm/api/users/alice", json={"action": "quota", "value": None}, headers=X)
    assert "quota" not in fake.membership("alice", "icm")["data"]
    assert root.post("/icm/api/users/alice", json={"action": "block"}, headers=X).status_code == 200
    assert as_user(fake, a, "alice").get("/icm/api/me").json()["membership"] == "blocked"
    assert root.post("/icm/api/users/alice", json={"action": "unblock"}, headers=X).status_code == 200
    assert root.post("/icm/api/users/alice", json={"action": "unblock"}, headers=X).status_code == 409
    assert root.post("/icm/api/users/ghost", json={"action": "block"}, headers=X).status_code == 404
    assert root.post("/icm/api/users/vera", json={"action": "delete"}, headers=X).status_code == 200
    assert "icm" not in fake.users[fake.uid("vera")]["memberships"]
    assert root.post("/icm/api/users/alice", json={"action": "fly"}, headers=X).status_code == 400


def test_the_admin_cannot_lock_themselves_out(monkeypatch):
    fake, a = world(monkeypatch)
    root = as_user(fake, a, "root")
    for action, value in (("role", "viewer"), ("block", None), ("delete", None)):
        assert root.post("/icm/api/users/root", json={"action": action, "value": value}, headers=X).status_code == 409


def test_roles_and_quota_apply_to_projects(monkeypatch):
    fake, a = world(monkeypatch)
    monkeypatch.setattr("icm.corpus.get", lambda wid: {"wid": wid})
    monkeypatch.setattr("icm.jobs.enqueue", lambda pid, kind="advance": None)
    alice, vera, out = as_user(fake, a, "alice"), as_user(fake, a, "vera"), as_user(fake, a, "outsider")
    assert alice.post("/icm/api/project", json={"seed": "v02:1"}, headers=X).status_code == 200
    fake.membership("alice", "icm")["data"]["quota"] = 1
    central.client().forget("icm", username="alice")
    assert alice.post("/icm/api/project", json={"seed": "v02:2"}, headers=X).status_code == 429
    r = vera.post("/icm/api/project", json={"seed": "v02:3"}, headers=X)
    assert r.status_code == 403 and "only read" in r.json()["detail"]
    assert out.post("/icm/api/project", json={"seed": "v02:3"}, headers=X).status_code == 401
    assert as_user(fake, a, "newbie").post("/icm/api/project", json={"seed": "v02:3"}, headers=X).status_code == 401


def test_logout_ends_the_central_session(monkeypatch):
    fake, a = world(monkeypatch)
    c = as_user(fake, a, "alice")
    cookie = c.cookies.get("ebad_sso")
    assert c.post("/icm/api/auth/logout").status_code == 403                         # X-ICM
    r = c.post("/icm/api/auth/logout", headers=X)
    assert r.status_code == 200 and cookie not in fake.sessions and "ebad_sso=" in r.headers["set-cookie"]


def test_a_down_service_is_a_503_not_a_crash(monkeypatch):
    fake, a = world(monkeypatch)
    root = as_user(fake, a, "root")
    import httpx
    from ebad_users.client import UsersClient
    def boom(request):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(central, "_client", UsersClient("http://x", "k", apps=("icm",), ttl=0, negative_ttl=0, transport=httpx.MockTransport(boom)))
    assert root.get("/icm/api/users").status_code in (401, 503)
