"""The wizard's own accounts (config.ACCOUNTS): sign-up -> Bale -> admin approval, roles, quotas, the users menu."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ICM_OPEN"] = "0"
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:
    print("fastapi not installed: skipped"); sys.exit(0)
from icm import config, project, users

from icm.api import make_router

X = {"X-ICM": "1"}
HELPER = "h" * 32


def app(monkeypatch, sent=None):
    d = tempfile.mkdtemp(prefix="icmusers_")
    monkeypatch.setattr(config, "CACHE_DIR", d)
    monkeypatch.setattr(config, "JOBS_DB", os.path.join(d, "jobs.sqlite"))
    monkeypatch.setattr(config, "USERS_DB", os.path.join(d, "users.sqlite"))
    monkeypatch.setattr(config, "ACCOUNTS", True)
    monkeypatch.setenv("ICM_HELPER_TOKEN", HELPER)
    monkeypatch.setenv("ICM_BALE_BOT_USERNAME", "testbot")
    monkeypatch.delenv("ICM_ADMIN", raising=False)
    monkeypatch.setattr(users, "notify", lambda chats, text: (sent.append((list(chats), text)) if sent is not None else None) or True)
    a = FastAPI()
    a.include_router(make_router(lambda req: {"username": "host", "is_admin": True}))     # ignored with accounts
    return TestClient(a)


def signup(c, name, chat, pw="secret-pw-1"):
    r = c.post("/icm/api/auth/register", json={"username": name, "name": name.title(), "password": pw}, headers=X)
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert r.json()["link"] == f"https://ble.ir/testbot?start={code}" and code.startswith("icm-")
    b = c.post("/icm/api/auth/bale", json={"code": code, "chat_id": chat, "handle": name}, headers={"X-ICM-Helper": HELPER})
    assert b.status_code == 200 and b.json()["ok"], b.text
    return code


def login(c, name, pw="secret-pw-1"):
    return c.post("/icm/api/auth/login", json={"username": name, "password": pw}, headers=X)


def test_signup_bale_approval_and_menu(monkeypatch):
    sent = []
    c = app(monkeypatch, sent)
    assert c.get("/icm/api/me").json()["user"] is None                      # the host's user is not used
    signup(c, "root", 100)                                                    # the first account: admin, active at once
    assert login(c, "root").status_code == 200
    me = c.get("/icm/api/me").json()
    assert me["user"] == "root" and me["admin"] and me["role"] == "admin" and me["accounts"] and me["pending"] == 0

    alice = TestClient(c.app)
    r = alice.post("/icm/api/auth/register", json={"username": "alice", "name": "Alice", "password": "secret-pw-1"}, headers=X)
    code = r.json()["code"]
    assert alice.get("/icm/api/auth/link-status", params={"code": code}).json()["status"] == "bale"
    assert login(alice, "alice").json()["need_bale"]["code"].startswith("icm-")   # a fresh code; the old one is gone
    assert c.post("/icm/api/auth/bale", json={"code": code, "chat_id": 200}, headers={"X-ICM-Helper": HELPER}).json()["ok"] is False
    code = login(alice, "alice").json()["need_bale"]["code"]
    assert c.post("/icm/api/auth/bale", json={"code": code, "chat_id": 100}, headers={"X-ICM-Helper": HELPER}).json()["ok"] is False   # root's chat
    assert c.post("/icm/api/auth/bale", json={"code": code, "chat_id": 200}).status_code == 401                                         # no helper token
    assert c.post("/icm/api/auth/bale", json={"code": code, "chat_id": 200, "handle": "al"}, headers={"X-ICM-Helper": HELPER}).json()["status"] == "pending"
    assert c.post("/icm/api/auth/bale", json={"code": code, "chat_id": 200}, headers={"X-ICM-Helper": HELPER}).json()["ok"] is False   # single use
    assert sent[-1][0] == [100] and "alice" in sent[-1][1]                    # the admins hear of it
    assert alice.get("/icm/api/auth/link-status", params={"code": code}).json()["status"] == "pending"
    assert login(alice, "alice").status_code == 403                           # waits for the admin

    assert c.get("/icm/api/me").json()["pending"] == 1
    assert alice.get("/icm/api/users").status_code == 401
    lst = c.get("/icm/api/users").json()["users"]
    assert [u["username"] for u in lst] == ["alice", "root"] and lst[0]["bale"] and lst[0]["bale_handle"] == "al"
    assert c.post("/icm/api/users/alice", json={"action": "approve"}).status_code == 403                       # X-ICM
    assert c.post("/icm/api/users/alice", json={"action": "approve"}, headers=X).json()["status"] == "active"
    assert sent[-1][0] == [200]
    assert login(alice, "alice").status_code == 200
    assert alice.get("/icm/api/me").json()["user"] == "alice"
    assert alice.get("/icm/api/users").status_code == 403

    # the root may not lock themselves out
    assert c.post("/icm/api/users/root", json={"action": "role", "value": "viewer"}, headers=X).status_code == 409
    assert c.post("/icm/api/users/root", json={"action": "block"}, headers=X).status_code == 409
    # activity: projects per owner
    project.create("v02:1", "alice")
    a = next(u for u in c.get("/icm/api/users").json()["users"] if u["username"] == "alice")
    assert a["projects"] == 1 and a["open_projects"] == 1 and a["last_login"] and a["sessions"] == 1

    # block: the session dies at once; unblock brings it back to active (not the session)
    assert c.post("/icm/api/users/alice", json={"action": "block"}, headers=X).json()["status"] == "blocked"
    assert alice.get("/icm/api/me").json()["user"] is None
    assert login(alice, "alice").status_code == 403
    assert c.post("/icm/api/users/alice", json={"action": "unblock"}, headers=X).json()["status"] == "active"
    assert login(alice, "alice").status_code == 200

    # logout
    assert alice.post("/icm/api/auth/logout", headers=X).status_code == 200
    assert alice.get("/icm/api/me").json()["user"] is None


def test_roles_and_quota(monkeypatch):
    c = app(monkeypatch)
    signup(c, "root", 1)
    login(c, "root")
    bob = TestClient(c.app)
    signup(bob, "bob", 2)
    c.post("/icm/api/users/bob", json={"action": "approve"}, headers=X)
    login(bob, "bob")
    monkeypatch.setattr("icm.corpus.get", lambda wid: {"wid": wid})
    monkeypatch.setattr("icm.jobs.enqueue", lambda pid, kind="advance": None)
    assert bob.post("/icm/api/project", json={"seed": "v02:1"}, headers=X).status_code == 200
    assert c.post("/icm/api/users/bob", json={"action": "quota", "value": 1}, headers=X).json()["quota"] == 1
    assert bob.get("/icm/api/me").json()["quota"] == 1
    assert bob.post("/icm/api/project", json={"seed": "v02:2"}, headers=X).status_code == 429
    for bad in (-1, "3", 1001, True):
        assert c.post("/icm/api/users/bob", json={"action": "quota", "value": bad}, headers=X).status_code == 400, bad
    c.post("/icm/api/users/bob", json={"action": "quota", "value": None}, headers=X)
    assert c.post("/icm/api/users/bob", json={"action": "role", "value": "viewer"}, headers=X).json()["role"] == "viewer"
    r = bob.post("/icm/api/project", json={"seed": "v02:2"}, headers=X)
    assert r.status_code == 403 and "only read" in r.json()["detail"]
    assert bob.post("/icm/api/auth/password", json={"old": "secret-pw-1", "new": "another-pw"}, headers=X).status_code == 200   # a viewer still may
    assert c.post("/icm/api/users/bob", json={"action": "role", "value": "god"}, headers=X).status_code == 400
    assert c.post("/icm/api/users/bob", json={"action": "delete"}, headers=X).json()["deleted"] == "bob"
    assert bob.get("/icm/api/me").json()["user"] is None


def test_register_rules_password_reset_and_brake(monkeypatch):
    sent = []
    c = app(monkeypatch, sent)
    for name, pw in (("ab", "secret-pw-1"), ("Ali Ahmadi", "secret-pw-1"), ("ali", "short")):
        assert c.post("/icm/api/auth/register", json={"username": name, "name": "x", "password": pw}, headers=X).status_code == 400
    assert c.post("/icm/api/auth/register", json={"username": "ali", "name": "x", "password": "secret-pw-1"}).status_code == 403   # X-ICM
    signup(c, "ali", 7)
    assert c.post("/icm/api/auth/register", json={"username": "ALI", "name": "x", "password": "secret-pw-1"}, headers=X).status_code == 409

    # forgotten password: a code through Bale; unknown names look the same
    assert c.post("/icm/api/auth/forgot", json={"username": "nobody"}, headers=X).json()["ok"]
    assert not sent
    assert c.post("/icm/api/auth/forgot", json={"username": "ali"}, headers=X).json()["ok"]
    assert sent[-1][0] == [7]
    code = sent[-1][1].split(": ")[1][:6]
    assert c.post("/icm/api/auth/forgot", json={"username": "ali"}, headers=X).status_code == 429            # too soon
    assert c.post("/icm/api/auth/reset", json={"username": "ali", "code": "000000" if code != "000000" else "111111", "password": "new-secret-1"}, headers=X).status_code == 403
    assert c.post("/icm/api/auth/reset", json={"username": "ali", "code": code, "password": "new-secret-1"}, headers=X).status_code == 200
    assert c.post("/icm/api/auth/reset", json={"username": "ali", "code": code, "password": "new-secret-2"}, headers=X).status_code == 403   # used
    assert login(c, "ali", "new-secret-1").status_code == 200

    # ten wrong passwords: the brake
    for _ in range(10):
        assert login(c, "ali", "wrong-password").status_code == 401
    assert login(c, "ali", "new-secret-1").status_code == 429
    from icm import api
    api._fails.clear()


def test_named_admin_and_open_mode_off(monkeypatch):
    c = app(monkeypatch)
    monkeypatch.setenv("ICM_ADMIN", "boss")
    signup(c, "early", 1)                                                    # not named: waits like anyone
    assert login(c, "early").status_code == 403
    signup(c, "boss", 2)
    assert login(c, "boss").status_code == 200 and c.get("/icm/api/me").json()["admin"]
    assert users.check_password("x" * 8, users.hash_password("x" * 8)) and not users.check_password("y" * 8, users.hash_password("x" * 8))
