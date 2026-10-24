"""The wizard's own accounts (``config.ACCOUNTS``): sign-up, confirmation through the Bale bot,
approval by an admin (the first account to confirm Bale, or those named in ICM_ADMIN, become admins
by themselves), sessions and per-user settings, in one SQLite file (``config.USERS_DB``).

    register(username, name, password)   -> link code; the account waits for Bale ("bale")
    confirm_bale(code, chat_id, handle)  -> the bot saw «/start <code>»: the account waits for an admin
                                            ("pending"); the very first account becomes the admin, active
    login(username, password)            -> session id (only "active" accounts)
    user_for(sid)                        -> the session's account, or None

Statuses: bale -> pending -> active <-> blocked. Roles: admin (everything and the users menu), editor
(makes projects, changes their own), viewer (reads only). ``quota`` overrides MAX_OPEN for one user.
Passwords are scrypt hashes; session ids, link and reset codes are stored only as SHA-256 hashes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.request
from contextlib import closing
from typing import Optional

from . import config

ROLES = ("admin", "editor", "viewer")
STATUSES = ("bale", "pending", "active", "blocked")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
CODE_PREFIX = "icm-"                       # the bot sends «/start icm-…» here, anything else to the workspace
LINK_TTL = 3 * 86400
RESET_TTL = 15 * 60
RESET_GAP = 60                             # seconds between two reset codes for one account
SESSION_TTL = 30 * 86400
SEEN_EVERY = 300                           # last_seen is written at most this often


class UserError(Exception):
    """A refusal the page shows the user; ``status`` is the HTTP status."""
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    d = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${d.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, want = stored.split("$")
        d = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(d.hex(), want)


def _check_password_rules(pw) -> None:
    if not isinstance(pw, str) or len(pw) < 8 or len(pw) > 200:
        raise UserError("گذرواژه باید دست‌کم ۸ نویسه باشد.")


def _db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(config.USERS_DB)), exist_ok=True)
    c = sqlite3.connect(config.USERS_DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript("""
        create table if not exists users(username text primary key, name text not null, pw text not null,
            role text not null default 'editor', status text not null default 'bale', quota integer,
            bale_chat integer unique, bale_handle text, created real not null, approved real, approved_by text,
            last_login real, last_seen real);
        create table if not exists sessions(sid text primary key, username text not null, created real not null, expires real not null);
        create table if not exists codes(code text primary key, username text not null, purpose text not null,
            created real not null, expires real not null, used real, tries integer not null default 0);
        create index if not exists codes_user on codes(username, purpose);
        create index if not exists sessions_user on sessions(username);""")
    return c


def _row(c, username: str):
    return c.execute("select * from users where username=?", (username,)).fetchone()


def public(u) -> dict:
    """What the page may see of an account."""
    return dict(username=u["username"], name=u["name"], role=u["role"], status=u["status"],
                is_admin=u["role"] == "admin" and u["status"] == "active", quota=u["quota"],
                bale=bool(u["bale_chat"]), bale_handle=u["bale_handle"])


def bot_username() -> str:
    return os.environ.get("ICM_BALE_BOT_USERNAME") or os.environ.get("EBAD_BALE_BOT_USERNAME") or ""


def _link(code: str) -> dict:
    bot = bot_username()
    return dict(code=code, bot=bot, link=f"https://ble.ir/{bot}?start={code}" if bot else None, expires_in=LINK_TTL)


def _new_link_code(c, username: str) -> dict:
    code = CODE_PREFIX + secrets.token_urlsafe(9)          # 4 + 12 url-safe characters: fits a Bale /start payload
    c.execute("delete from codes where username=? and purpose='link'", (username,))
    c.execute("insert into codes(code, username, purpose, created, expires) values (?,?,?,?,?)",
              (_h(code), username, "link", time.time(), time.time() + LINK_TTL))
    return _link(code)


# --- sign-up ----------------------------------------------------------------------------------------
def register(username, name, password) -> dict:
    username = (username or "").strip().lower() if isinstance(username, str) else ""
    name = (name or "").strip() if isinstance(name, str) else ""
    if not USERNAME_RE.match(username):
        raise UserError("نام کاربری باید ۳ تا ۳۲ نویسه از حروف کوچک لاتین، رقم و . _ - باشد.")
    if not name or len(name) > 80:
        raise UserError("نام نمایشی را بنویسید (تا ۸۰ نویسه).")
    _check_password_rules(password)
    with closing(_db()) as c, c:
        if _row(c, username):
            raise UserError("این نام کاربری گرفته شده است.", 409)
        c.execute("insert into users(username, name, pw, created) values (?,?,?,?)", (username, name, hash_password(password), time.time()))
        return _new_link_code(c, username)


def link_status(code: str) -> dict:
    """The sign-up page polls this with the code it was given."""
    with closing(_db()) as c:
        row = c.execute("select username from codes where code=? and purpose='link'", (_h(code or ""),)).fetchone()
        u = row and _row(c, row["username"])
        if not u:
            raise UserError("این کد شناخته نیست.", 404)
        return dict(status=u["status"], username=u["username"])


def confirm_bale(code: str, chat_id, handle: Optional[str] = None) -> dict:
    """The Bale bot received «/start <code>» from ``chat_id``. Returns {ok, message} for the bot's reply."""
    if not isinstance(chat_id, int):
        raise UserError("chat_id must be an integer")
    with closing(_db()) as c, c:
        row = c.execute("select * from codes where code=? and purpose='link'", (_h(code or ""),)).fetchone()
        if not row or row["used"] or row["expires"] < time.time():
            return dict(ok=False, message="این کد معتبر نیست یا منقضی شده است. در صفحه‌ی ورود دوباره کد بگیرید.")
        u = _row(c, row["username"])
        other = c.execute("select username from users where bale_chat=? and username<>?", (chat_id, u["username"])).fetchone()
        if other:
            return dict(ok=False, message=f"این گفت‌وگوی بله پیش‌تر به حساب «{other['username']}» وصل شده است.")
        c.execute("update codes set used=? where code=?", (time.time(), row["code"]))
        named = [x.strip().lower() for x in os.environ.get("ICM_ADMIN", "").split(",") if x.strip()]
        first = u["username"] in named if named else not c.execute("select 1 from users where role='admin' and status='active'").fetchone()
        status = u["status"]
        if first:
            status = "active"
            c.execute("update users set role='admin', approved=?, approved_by='bale' where username=?", (time.time(), u["username"]))
        elif status == "bale":
            status = "pending"
        c.execute("update users set status=?, bale_chat=?, bale_handle=? where username=?", (status, chat_id, handle, u["username"]))
        admins = [r["bale_chat"] for r in c.execute("select bale_chat from users where role='admin' and status='active' and bale_chat is not null")]
    if status == "pending":
        notify(admins, f"کاربر تازه در «تحلیل منبع حدیث» منتظر تأیید است: {u['name']} ({u['username']}).")
        return dict(ok=True, status=status, message=f"✅ بله‌ی شما برای حساب «{u['username']}» تأیید شد. پس از تأیید مدیر می‌توانید وارد شوید.")
    return dict(ok=True, status=status, message=f"✅ بله‌ی شما به حساب «{u['username']}» وصل شد." + (" شما نخستین کاربر و مدیر سامانه هستید." if first else ""))


# --- sessions -----------------------------------------------------------------------------------
def login(username, password) -> dict:
    """{sid} for an active account; {need_bale: link} while the account still waits for Bale."""
    username = username.strip().lower() if isinstance(username, str) else ""
    with closing(_db()) as c, c:
        u = _row(c, username)
        if not u or not isinstance(password, str) or not check_password(password, u["pw"]):
            raise UserError("نام کاربری یا گذرواژه نادرست است.", 401)
        if u["status"] == "bale":
            return dict(need_bale=_new_link_code(c, username))
        if u["status"] == "pending":
            raise UserError("حساب شما منتظر تأیید مدیر است.", 403)
        if u["status"] == "blocked":
            raise UserError("حساب شما بسته شده است.", 403)
        sid = secrets.token_urlsafe(32)
        now = time.time()
        c.execute("delete from sessions where expires<?", (now,))
        c.execute("insert into sessions values (?,?,?,?)", (_h(sid), username, now, now + SESSION_TTL))
        c.execute("update users set last_login=?, last_seen=? where username=?", (now, now, username))
        return dict(sid=sid, user=public(u))


def logout(sid: str) -> None:
    with closing(_db()) as c, c:
        c.execute("delete from sessions where sid=?", (_h(sid or ""),))


def user_for(sid: Optional[str]) -> Optional[dict]:
    if not sid:
        return None
    with closing(_db()) as c, c:
        s = c.execute("select * from sessions where sid=?", (_h(sid),)).fetchone()
        if not s or s["expires"] < time.time():
            return None
        u = _row(c, s["username"])
        if not u or u["status"] != "active":
            return None
        if not u["last_seen"] or time.time() - u["last_seen"] > SEEN_EVERY:
            c.execute("update users set last_seen=? where username=?", (time.time(), u["username"]))
        return public(u)


def change_password(username: str, old, new) -> None:
    _check_password_rules(new)
    with closing(_db()) as c, c:
        u = _row(c, username)
        if not u or not isinstance(old, str) or not check_password(old, u["pw"]):
            raise UserError("گذرواژه‌ی کنونی نادرست است.", 403)
        c.execute("update users set pw=? where username=?", (hash_password(new), username))


# --- forgotten password: a code through Bale ----------------------------------------------------
def send_reset(username) -> None:
    """Sends a six-digit code to the account's Bale chat. Silent when there is nothing to send to
    (the page always says the same thing, so it does not tell which usernames exist)."""
    username = username.strip().lower() if isinstance(username, str) else ""
    with closing(_db()) as c, c:
        u = _row(c, username)
        if not u or not u["bale_chat"] or u["status"] in ("bale", "blocked"):
            return
        last = c.execute("select max(created) m from codes where username=? and purpose='reset'", (username,)).fetchone()["m"]
        if last and time.time() - last < RESET_GAP:
            raise UserError("کمی صبر کنید و دوباره درخواست کنید.", 429)
        code = "%06d" % secrets.randbelow(10 ** 6)
        c.execute("delete from codes where username=? and purpose='reset'", (username,))
        c.execute("insert into codes(code, username, purpose, created, expires) values (?,?,?,?,?)",
                  (_h(username + ":" + code), username, "reset", time.time(), time.time() + RESET_TTL))
        chat = u["bale_chat"]
    if not notify([chat], f"کد بازیابی گذرواژه‌ی «تحلیل منبع حدیث» برای {username}: {code}\n(۱۵ دقیقه اعتبار دارد؛ اگر شما درخواست نکرده‌اید، نادیده بگیرید.)"):
        raise UserError("فرستادن پیام بله ممکن نشد؛ بعداً دوباره تلاش کنید.", 502)


def reset_password(username, code, new) -> None:
    username = username.strip().lower() if isinstance(username, str) else ""
    _check_password_rules(new)
    with closing(_db()) as c, c:
        row = c.execute("select * from codes where username=? and purpose='reset' and used is null", (username,)).fetchone()
        if not row or row["expires"] < time.time() or row["tries"] >= 5:
            raise UserError("کد نامعتبر یا منقضی است؛ دوباره درخواست کنید.", 403)
        if not isinstance(code, str) or not hmac.compare_digest(row["code"], _h(username + ":" + code.strip())):
            c.execute("update codes set tries=tries+1 where code=?", (row["code"],))
            raise UserError("کد نادرست است.", 403)
        c.execute("update codes set used=? where code=?", (time.time(), row["code"]))
        c.execute("update users set pw=? where username=?", (hash_password(new), username))
        c.execute("delete from sessions where username=?", (username,))


# --- the admin's menu ---------------------------------------------------------------------------
def list_users(projects: list) -> list:
    """Every account with its activity; ``projects`` is project.list_projects() (owners, statuses)."""
    n, n_open = {}, {}
    for p in projects:
        n[p["owner"]] = n.get(p["owner"], 0) + 1
        if p.get("status") in ("draft", "running"):          # as MAX_OPEN counts them
            n_open[p["owner"]] = n_open.get(p["owner"], 0) + 1
    with closing(_db()) as c:
        rows = c.execute("select * from users order by case status when 'pending' then 0 when 'bale' then 1 when 'active' then 2 else 3 end, created desc").fetchall()
        sess = {r["username"]: r["n"] for r in c.execute("select username, count(*) n from sessions where expires>? group by username", (time.time(),))}
    return [dict(public(u), created=u["created"], approved=u["approved"], approved_by=u["approved_by"], last_login=u["last_login"],
                 last_seen=u["last_seen"], projects=n.get(u["username"], 0), open_projects=n_open.get(u["username"], 0),
                 sessions=sess.get(u["username"], 0)) for u in rows]


def count_pending() -> int:
    with closing(_db()) as c:
        return c.execute("select count(*) from users where status='pending'").fetchone()[0]


def admin_action(actor: str, username: str, action: str, value=None) -> dict:
    """approve | block | unblock | role (value: a role) | quota (value: int or None) | delete."""
    with closing(_db()) as c, c:
        u = _row(c, username)
        if not u:
            raise UserError("چنین کاربری نیست.", 404)
        if username == actor and action in ("block", "role", "delete"):
            raise UserError("نقش یا وضعیت حساب خودتان را از این‌جا نمی‌توانید تغییر دهید.", 409)
        if action == "approve":
            if u["status"] != "pending":
                raise UserError("این حساب منتظر تأیید نیست.", 409)
            c.execute("update users set status='active', approved=?, approved_by=? where username=?", (time.time(), actor, username))
            chat = u["bale_chat"]
        elif action == "block":
            c.execute("update users set status='blocked' where username=?", (username,))
            c.execute("delete from sessions where username=?", (username,))
            chat = None
        elif action == "unblock":
            if u["status"] != "blocked":
                raise UserError("این حساب بسته نیست.", 409)
            c.execute("update users set status=? where username=?", ("active" if u["bale_chat"] else "bale", username))
            chat = None
        elif action == "role":
            if value not in ROLES:
                raise UserError("نقش نامعتبر است.")
            c.execute("update users set role=? where username=?", (value, username))
            chat = None
        elif action == "quota":
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1000):
                raise UserError("سهمیه باید عددی از ۰ تا ۱۰۰۰ یا خالی (پیش‌فرض) باشد.")
            c.execute("update users set quota=? where username=?", (value, username))
            chat = None
        elif action == "delete":
            c.execute("delete from sessions where username=?", (username,))
            c.execute("delete from codes where username=?", (username,))
            c.execute("delete from users where username=?", (username,))
            return dict(ok=True, deleted=username)
        else:
            raise UserError("کار نامعتبر است.")
        out = public(_row(c, username))
    if chat:
        notify([chat], f"✅ حساب «{username}» در «تحلیل منبع حدیث» تأیید شد؛ اکنون می‌توانید وارد شوید.")
    return out


# --- Bale messages ------------------------------------------------------------------------------
def notify(chats, text: str) -> bool:
    """Best effort: True when every message went out (False without a bot token)."""
    token = os.environ.get("ICM_BALE_BOT_TOKEN") or os.environ.get("EBAD_BALE_BOT_TOKEN")
    if not token:
        return False
    ok = True
    for chat in chats:
        try:
            req = urllib.request.Request(f"https://tapi.bale.ai/bot{token}/sendMessage", data=json.dumps(dict(chat_id=chat, text=text)).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                ok = ok and bool(json.load(r).get("ok"))
        except Exception:
            ok = False
    return ok
