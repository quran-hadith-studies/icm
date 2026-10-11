"""Git synchronisation of the cache repository (quran-hadith-studies/icm-cache).

Only the worker commits. After a job: commit everything under the cache, rebase on the remote, push.
A failure is logged and retried after the next job; results are served from disk regardless."""
from __future__ import annotations

import os
import subprocess
from typing import Tuple

from . import config

NAME = os.environ.get("ICM_GIT_NAME", "icm-bot")
EMAIL = os.environ.get("ICM_GIT_EMAIL", "icm-bot@users.noreply.github.com")


def enabled() -> bool:
    return os.path.isdir(os.path.join(config.CACHE_DIR, ".git")) and os.environ.get("ICM_GIT", "1") != "0"


def _git(*args: str, timeout: int = 120) -> Tuple[int, str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    p = subprocess.run(["git", "-c", f"user.name={NAME}", "-c", f"user.email={EMAIL}", *args], cwd=config.CACHE_DIR,
                       capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, (p.stdout + p.stderr).strip()


def pull() -> str:
    if not enabled():
        return "git disabled"
    code, out = _git("pull", "--rebase", "--autostash", "-q")
    if code:
        _git("rebase", "--abort")
    return out or "ok"


def commit_push(message: str) -> Tuple[bool, str]:
    if not enabled():
        return False, "git disabled"
    _git("add", "-A")
    code, out = _git("diff", "--cached", "--quiet")
    if code == 0:
        return True, "nothing to commit"
    code, out = _git("commit", "-q", "-m", message)
    if code:
        return False, out
    code, out = _git("pull", "--rebase", "-q")
    if code:
        _git("rebase", "--abort")
        return False, "rebase failed: " + out
    code, out = _git("push", "-q", "origin", "HEAD")
    return code == 0, out or "pushed"


def pull_from_server(remote: str, timeout: int = 900) -> Tuple[bool, str]:
    """Copy the server's cache directory into this one (laptop side).

    ``remote`` = ``user@host:path/to/cache``. Files are only ever added or replaced by a newer
    version (``--update``), never deleted; stage, decision and run files are immutable on the
    server, so existing copies are authoritative."""
    cmd = ["rsync", "-a", "--update", "--exclude=.git", "--exclude=.lock", "--exclude=*.tmp",
           remote.rstrip("/") + "/", config.CACHE_DIR.rstrip("/") + "/"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode == 0, (p.stdout + p.stderr).strip()
