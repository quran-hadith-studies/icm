"""The wizard's accounts when ``config.AUTH == "central"``: people, roles and quotas live in the central users service
(membership of the app ``icm``: roles admin / editor / viewer, ``data.quota``). The host site hands each request's
user in (``get_user``); this module only runs the admin's users menu through the service and ends sessions."""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Optional

APP = "icm"
ROLES = ("admin", "editor", "viewer")
_client = None
_pending_memo: tuple[float, int] = (0.0, 0)


class CentralError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def client():
    global _client
    if _client is None:
        from ebad_users.client import UsersClient
        _client = UsersClient(apps=(APP,))
    return _client


def role_of(roles: list[str]) -> str:
    return "admin" if "admin" in roles else "editor" if "editor" in roles else "viewer"


def urls(next_: str = "") -> dict:
    key = os.environ.get("ICM_SIGNUP_KEY", "")
    c = client()
    return dict(login=c.login_url(next_ or "/icm"), signup=c.signup_url(APP, key, next_ or "/icm") if key else "",
                account=c.public_url + "/account")


def user_from_state(state_user: Optional[dict]) -> Optional[dict]:
    """The host's request user (compat dict with ``memberships``) as the wizard's user, or None when not a member.
    ``status`` is the membership's, so the wizard can tell «waiting for approval» from «not signed in»."""
    if not state_user:
        return None
    m = (state_user.get("memberships") or {}).get(APP)
    if not m:
        return None
    role = role_of(m.get("roles", []))
    return dict(username=state_user["id"], handle=state_user["username"].lower(), id=state_user["id"], name=(state_user.get("profile") or {}).get("display_name"),
                role=role, is_admin=bool(state_user.get("is_site_admin")) or role == "admin", quota=(m.get("data") or {}).get("quota"),
                status=m.get("status", "pending"))


def _epoch(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _row(key: str) -> Optional[dict]:
    text = (key or "").strip().lower()
    if not text:
        return None
    if text.startswith("usr_"):
        return client().users_by_ids(APP, [text]).get(text)
    return client().users_by_usernames(APP, [text]).get(text)


_names_memo: tuple[float, dict] = (0.0, {})


def names() -> dict:
    """id -> display name of the app's members, for showing who made what (the app's data holds ids only)."""
    global _names_memo
    now = time.time()
    if now - _names_memo[0] < 30:
        return _names_memo[1]
    try:
        rows = client().members(APP)["members"]
        _names_memo = (now, {r["id"]: r.get("display_name") or r["username"] for r in rows})
    except Exception:
        pass
    return _names_memo[1]


def _status(row: dict) -> str:
    return {"active": "active", "pending": "pending", "blocked": "blocked", "rejected": "blocked"}.get(row["status"], "pending")


def list_users(projects: list) -> list:
    n: dict = {}
    n_open: dict = {}
    for p in projects:
        n[p["owner"]] = n.get(p["owner"], 0) + 1
        if p.get("status") in ("draft", "running"):
            n_open[p["owner"]] = n_open.get(p["owner"], 0) + 1
    rows = client().members(APP, with_=("data", "messengers"))["members"]
    out = []
    for r in rows:
        bale = next((m for m in r.get("messengers", []) if m["platform"] == "bale"), None)
        role = role_of(r.get("roles", []))
        out.append(dict(username=r["id"], handle=r["username"], name=r["display_name"], role=role, status=_status(r), is_admin=role == "admin" and r["status"] == "active",
                        quota=(r.get("data") or {}).get("quota"), bale=bool(bale), bale_handle=bale and bale.get("handle"),
                        created=_epoch(r.get("requested_at")), approved=_epoch(r.get("approved_at")), approved_by=None, last_login=None, last_seen=None,
                        projects=n.get(r["id"], 0), open_projects=n_open.get(r["id"], 0), sessions=0))
    order = {"pending": 0, "active": 1, "blocked": 2}
    out.sort(key=lambda u: (order.get(u["status"], 3), -(u["created"] or 0)))
    return out


def count_pending() -> int:
    global _pending_memo
    now = time.time()
    if now - _pending_memo[0] < 20:
        return _pending_memo[1]
    n = len(client().members(APP, status="pending")["members"])
    _pending_memo = (now, n)
    return n


def admin_action(actor: dict, username: str, action: str, value: Any = None) -> dict:
    """approve | block | unblock | role (value: a role) | quota (value: int or None) | delete."""
    from ebad_users.client import UsersError, UsersUnavailable
    global _pending_memo
    row = _row(username)
    if not row:
        raise CentralError("چنین کاربری نیست.", 404)
    if row["id"] == actor["id"] and action in ("block", "role", "delete"):
        raise CentralError("نقش یا وضعیت حساب خودتان را از این‌جا نمی‌توانید تغییر دهید.", 409)
    c, uid, by = client(), row["id"], actor["id"]
    try:
        if action == "approve":
            if row["status"] != "pending":
                raise CentralError("این حساب منتظر تأیید نیست.", 409)
            c.member_action(APP, uid, "approve", actor=by)
        elif action == "block":
            c.member_action(APP, uid, "block", actor=by)
        elif action == "unblock":
            if row["status"] != "blocked":
                raise CentralError("این حساب بسته نیست.", 409)
            c.member_action(APP, uid, "unblock", actor=by)
        elif action == "role":
            if value not in ROLES:
                raise CentralError("نقش نامعتبر است.", 400)
            c.set_roles(APP, uid, [value], actor=by)
        elif action == "quota":
            if value is not None:
                if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1000:
                    raise CentralError("سقف باید عددی از ۰ تا ۱۰۰۰ باشد.", 400)
            c.patch_member_data(APP, uid, {"quota": value}, actor=by)
        elif action == "delete":
            c.delete_member(APP, uid, actor=by)
        else:
            raise CentralError("کار ناشناخته.", 400)
    except UsersError as e:
        raise CentralError("اجازهٔ این کار را ندارید." if e.status in (403, 404) else str(e), 403 if e.status in (403, 404) else e.status)
    except UsersUnavailable:
        raise CentralError("سامانهٔ کاربران در دسترس نیست.", 503)
    _pending_memo = (0.0, 0)
    c.forget(APP, username=row["username"], user_id=uid)
    return dict(ok=True)
