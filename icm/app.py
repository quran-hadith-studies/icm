"""The desktop app: runs the model on this computer when the website hands it a project.

    python3 -m icm app install        register the icm:// link and the .icmjob file type (once)
    python3 -m icm app                open the app window (a page on 127.0.0.1)
    python3 -m icm app open LINK|FILE hand it an icm:// link or an .icmjob file (what the OS does for you)
    python3 -m icm app uninstall

Flow: the website's button opens ``icm://run?server=...&pid=...&token=...``; the OS starts this app, which
fetches the input with the one-job token, shows "run", computes with all cores (same code as the browser
and the helper: ``icm.client``), then shows "upload" and posts the result with the same token. The result
is also saved as a file, so the website's "upload a result file" works as a fallback. Everything listens
on 127.0.0.1 only, behind a per-session secret and a Host check."""
from __future__ import annotations

import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.join(os.path.expanduser("~"), ".icm_app")
STATE_FILE = os.path.join(HOME, "app.json")
CONFIG_FILE = os.path.join(HOME, "config.json")
RESULTS = os.path.join(HOME, "results")
DEFAULT_SERVERS = ["https://ebadollah.ir"]
UA = "icm-app/1.0 (+https://github.com/quran-hadith-studies/icm)"
SCHEME, EXT = "icm", ".icmjob"
IDLE_EXIT = 5 * 60           # seconds without the page open (and nothing running) before the app quits


def allowed_servers() -> list:
    try:
        extra = json.load(open(CONFIG_FILE)).get("servers", [])
    except Exception:
        extra = []
    return DEFAULT_SERVERS + [s.rstrip("/") for s in extra]


def server_allowed(server: str) -> bool:
    u = urllib.parse.urlparse(server)
    origin = f"{u.scheme}://{u.netloc}"
    return origin in allowed_servers() or (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"))


def parse_link(link: str) -> dict:
    u = urllib.parse.urlparse(link)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
    if u.scheme != SCHEME or u.netloc != "run" or not all(q.get(k) for k in ("server", "pid", "token")):
        raise ValueError("not an icm://run link")
    return dict(server=q["server"].rstrip("/"), pid=q["pid"], token=q["token"])


def _http(server: str, path: str, token: str, body=None):
    req = urllib.request.Request(server + path, headers={"User-Agent": UA, "X-ICM-Job": token, "X-ICM": "1", "Content-Type": "application/json"},
                                 data=None if body is None else json.dumps(body).encode(), method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf8", "replace")
        raise RuntimeError(f"سرور پاسخ {e.code} داد: {detail}")


class App:
    def __init__(self):
        self.lock = threading.Lock()
        self.secret = secrets.token_urlsafe(24)
        self.s = dict(phase="idle", message="", frac=0.0, job=None, warn=None, result_file=None, log=[])
        self.input = None
        self.last_poll = time.time()
        self.job = None
        self.result = None

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.s))

    def set(self, **kw):
        with self.lock:
            self.s.update(kw)

    def log(self, m: str):
        with self.lock:
            self.s["log"] = (self.s["log"] + [m])[-40:]

    # -- loading a job ----------------------------------------------------------------------------
    def load_link(self, link: str):
        try:
            j = parse_link(link)
            if not server_allowed(j["server"]):
                raise ValueError(f"این کارگزار مجاز نیست: {j['server']}. برای افزودن، آن را در {CONFIG_FILE} زیر «servers» بنویسید.")
            self.set(phase="loading", message="در حال گرفتن ورودی از سرور…", frac=0.0, job=dict(j, token=None), warn=None, result_file=None)
            inp = _http(j["server"], f"/api/project/{j['pid']}/compute-input", j["token"])
            self._ready(j, inp)
        except Exception as e:
            self.set(phase="error", message=str(e))

    def load_file(self, path: str):
        try:
            data = json.load(open(path, encoding="utf8"))
            j = dict(server=data["server"].rstrip("/"), pid=data["pid"], token=data["token"])
            if not server_allowed(j["server"]):
                raise ValueError(f"این کارگزار مجاز نیست: {j['server']}")
            self._ready(j, data["input"])
        except Exception as e:
            self.set(phase="error", message=f"فایل کار خوانده نشد: {e}")

    def _ready(self, j: dict, inp: dict):
        self.input, self.job = inp, j
        warn = None
        try:
            from .build import model_zip
            if inp.get("model") and inp["model"] != model_zip.digest():
                warn = "نسخه‌ی مدل این برنامه با نسخه‌ی سرور یکی نیست؛ برنامه را به‌روز کنید (git pull) وگرنه نتیجه ممکن است پذیرفته نشود."
        except Exception:
            pass
        self.set(phase="ready", message="", frac=0.0, warn=warn, job=dict(server=j["server"], pid=j["pid"], preset=inp.get("preset")), result_file=None)

    # -- running and uploading --------------------------------------------------------------------
    def run(self):
        with self.lock:
            if self.s["phase"] not in ("ready", "failed", "done", "uploaded", "error") or self.input is None:
                return
            self.s.update(phase="running", message="آماده‌سازی…", frac=0.0, result_file=None)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        from . import helper
        try:
            workers = max(1, (os.cpu_count() or 2) - 1)
            t0 = time.time()
            res = helper.compute(dict(self.input, key=self.input["key"]), workers, say=self.log,
                                 progress=lambda k, n, name: self.set(message=f"{k} از {n} نامزد منبع محاسبه شد ({name})", frac=0.03 + 0.95 * k / n))
            res["provenance"].update(kind="desktop", app=UA[:40])
            os.makedirs(RESULTS, exist_ok=True)
            self.result = res
            path = os.path.join(RESULTS, f"{self.job['pid']}{EXT.replace('job', 'result')}")
            json.dump(res, open(path, "w", encoding="utf8"), ensure_ascii=False)
            self.set(phase="done", message=f"محاسبه تمام شد ({round(time.time() - t0)} ثانیه).", frac=1.0, result_file=path)
        except Exception as e:
            self.set(phase="failed", message=f"محاسبه شکست خورد: {e}")

    def upload(self):
        with self.lock:
            if self.s["phase"] not in ("done", "uploaded", "error") or not self.result:
                return
            self.s.update(phase="uploading", message="در حال فرستادن نتیجه…")
        threading.Thread(target=self._upload, daemon=True).start()

    def _upload(self):
        try:
            _http(self.job["server"], f"/api/project/{self.job['pid']}/compute-result", self.job["token"], self.result)
            self.set(phase="uploaded", message="نتیجه ثبت شد. می‌توانید به صفحه‌ی پروژه برگردید.")
        except Exception as e:
            self.set(phase="done", message=f"فرستادن شکست خورد: {e} — می‌توانید فایل نتیجه را در سایت بارگذاری کنید.")


APP = App()

PAGE = """<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ICM — برنامه‌ی رومیزی</title>
<style>body{font:16px/1.7 system-ui,Tahoma,sans-serif;max-width:640px;margin:2rem auto;padding:0 1rem;color:#222}
.card{border:1px solid #ccc;border-radius:10px;padding:1rem 1.2rem}button{font:inherit;padding:.5rem 1.2rem;border-radius:8px;border:1px solid #888;background:#f4f4f4;cursor:pointer}
button.primary{background:#1f6feb;color:#fff;border-color:#1f6feb}button:disabled{opacity:.5;cursor:default}.bar{height:10px;background:#eee;border-radius:5px;margin:.8rem 0}
.bar i{display:block;height:100%;background:#1f6feb;border-radius:5px;width:0}.muted{color:#666;font-size:14px}.warn{background:#fff4d6;padding:.5rem .8rem;border-radius:8px}
pre{font-size:12px;direction:ltr;max-height:10rem;overflow:auto;background:#f7f7f7;padding:.5rem}@media(prefers-color-scheme:dark){body{background:#111;color:#ddd}.card{border-color:#444}button{background:#222;color:#ddd}pre{background:#1a1a1a}.bar{background:#333}.muted{color:#999}.warn{background:#3a3320}}</style>
<h2>ICM — محاسبه روی این رایانه</h2><div class="card" id="c">…</div>
<script>
const S=%SECRET%;let st={};
async function call(p,b){const r=await fetch(p,{method:'POST',headers:{'X-App':S,'Content-Type':'application/json'},body:JSON.stringify(b||{})});return r.json()}
const T={idle:'کاری در انتظار نیست. در صفحه‌ی پروژه‌ی سایت، «اجرا با برنامه‌ی رومیزی» را بزنید، یا فایل «'+'.icmjob» را با این برنامه باز کنید.',
loading:'در حال گرفتن ورودی…',ready:'ورودی آماده است. محاسبه را شروع کنیم؟',running:'در حال محاسبه — پنجره را نبندید.',done:'محاسبه تمام شد. نتیجه را به سایت بفرستیم؟',
uploading:'در حال فرستادن…',uploaded:'انجام شد.',failed:'خطا',error:'خطا'};
function draw(){const j=st.job;const c=document.getElementById('c');c.replaceChildren();
const add=(t,x,cls)=>{const e=document.createElement(t);if(cls)e.className=cls;if(x!=null)e.textContent=x;c.append(e);return e};
add('p',T[st.phase]||st.phase);if(j)add('p',`پروژه ${j.pid} — دقت ${j.preset||''} — ${j.server}`,'muted');
if(st.warn)add('p',st.warn,'warn');
if(st.phase==='running'||st.phase==='uploading'){const b=add('div',null,'bar');const i=document.createElement('i');i.style.width=Math.round(100*(st.frac||0))+'%';b.append(i)}
if(st.message)add('p',st.message,'muted');
const row=add('div');
if(['ready','failed'].includes(st.phase)||(st.phase==='error'&&j)){const b=document.createElement('button');b.className='primary';b.textContent=st.phase==='ready'?'شروع محاسبه':'تلاش دوباره';b.onclick=()=>call('/run');row.append(b)}
if(['done','uploaded'].includes(st.phase)){const b=document.createElement('button');b.className='primary';b.textContent=st.phase==='done'?'فرستادن نتیجه به سایت':'دوباره بفرست';b.onclick=()=>call('/upload');row.append(b)}
if(st.result_file)add('p','فایل نتیجه: '+st.result_file,'muted');
if(st.log&&st.log.length){const p=add('pre');p.textContent=st.log.join('\\n')}}
async function tick(){try{st=await (await fetch('/state',{headers:{'X-App':S}})).json();draw()}catch(e){document.getElementById('c').textContent='ارتباط با برنامه قطع شد.'}setTimeout(tick,1000)}
tick();
</script></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _ok(self) -> bool:
        host = self.headers.get("Host", "")
        return host.split(":")[0] in ("127.0.0.1", "localhost") and host.endswith(":" + str(self.server.server_address[1]))

    def _send(self, code: int, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode("utf8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._ok():
            return self._send(403, dict(error="host"))
        if self.path == "/":
            return self._send(200, PAGE.replace("%SECRET%", json.dumps(APP.secret)), "text/html")
        if self.path == "/state" and self.headers.get("X-App") == APP.secret:
            APP.last_poll = time.time()
            return self._send(200, APP.snapshot())
        self._send(404, dict(error="not found"))

    def do_POST(self):
        if not self._ok() or self.headers.get("X-App") != APP.secret:
            return self._send(403, dict(error="forbidden"))
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/run":
            APP.run()
        elif self.path == "/upload":
            APP.upload()
        elif self.path == "/window":
            return self._send(200, dict(open=time.time() - APP.last_poll < 5))
        elif self.path == "/load":
            threading.Thread(target=(APP.load_link if body.get("link") else APP.load_file), args=(body.get("link") or body.get("file"),), daemon=True).start()
        else:
            return self._send(404, dict(error="not found"))
        self._send(200, dict(ok=True))


def _chromium() -> str | None:
    """A Chromium-family browser that can show a page as a plain app window (no tabs, no address bar)."""
    names = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "microsoft-edge-stable", "brave-browser")
    paths = []
    if platform.system() == "Darwin":
        for n in ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium"):
            paths += [f"/Applications/{n}.app/Contents/MacOS/{n}", os.path.expanduser(f"~/Applications/{n}.app/Contents/MacOS/{n}")]
    elif platform.system() == "Windows":
        for root in filter(None, (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA"))):
            paths += [os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"),      # present on every current Windows
                      os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"),
                      os.path.join(root, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")]
    import shutil
    paths += [shutil.which(n) for n in names]
    return next((p for p in paths if p and os.path.exists(p)), None)


def open_window(url: str) -> None:
    """Show the page in its own window (Chrome/Edge/Brave ``--app`` mode, a private profile so the size is honoured);
    any other browser gets a normal tab."""
    exe = None if os.environ.get("BROWSER") else _chromium()      # BROWSER set: the user (or a test) chose the opener
    if exe:
        try:
            subprocess.Popen([exe, f"--app={url}", "--window-size=760,680", f"--user-data-dir={os.path.join(HOME, 'profile')}",
                              "--no-first-run", "--no-default-browser-check"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    webbrowser.open(url)


def _running() -> dict | None:
    try:
        st = json.load(open(STATE_FILE))
        with socket.create_connection(("127.0.0.1", st["port"]), timeout=1):
            return st
    except Exception:
        return None


def serve(target: str | None = None, open_browser: bool = True) -> int:
    """Start the app (or hand ``target`` to the one already running) and show its page."""
    live = _running()
    if live:                                                    # one app at a time: forward the link/file to it
        if target:
            key = "link" if target.startswith(SCHEME + "://") else "file"
            req = urllib.request.Request(f"http://127.0.0.1:{live['port']}/load", data=json.dumps({key: target}).encode(), method="POST",
                                         headers={"X-App": live["secret"], "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
        if open_browser:
            req = urllib.request.Request(f"http://127.0.0.1:{live['port']}/window", data=b"{}", method="POST",
                                         headers={"X-App": live["secret"], "Content-Type": "application/json"})
            if not json.load(urllib.request.urlopen(req, timeout=10)).get("open"):          # a window is already polling
                open_window(f"http://127.0.0.1:{live['port']}/")
        return 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    os.makedirs(HOME, exist_ok=True)
    fd = os.open(STATE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(dict(port=port, secret=APP.secret), f)
    if target:
        if target.startswith(SCHEME + "://"):
            threading.Thread(target=APP.load_link, args=(target,), daemon=True).start()
        else:
            threading.Thread(target=APP.load_file, args=(target,), daemon=True).start()
    print(f"ICM app on http://127.0.0.1:{port}/  (Ctrl-C to quit)", flush=True)
    if open_browser:
        open_window(f"http://127.0.0.1:{port}/")
    done = threading.Event()

    def idle_watch():                                           # browser-window mode: quit when the window was closed
        while True:
            time.sleep(30)
            busy = APP.s["phase"] in ("loading", "running", "uploading")
            if not busy and time.time() - APP.last_poll > IDLE_EXIT:
                done.set()
                return
    url = f"http://127.0.0.1:{port}/"
    try:
        if not open_browser:
            raise ImportError
        import webview                                           # a native window (WebView2 / WKWebView / GTK), no browser at all
    except ImportError:
        webview = None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        shown = False
        if webview:
            try:
                webview.create_window("ICM — محاسبه روی این رایانه", url, width=760, height=700, min_size=(520, 420))
                webview.start()                                  # blocks until the window is closed
                shown = True
            except Exception as e:                               # e.g. Linux without GTK/Qt: use a browser window instead
                print("native window unavailable:", e, flush=True)
        if not shown:
            if open_browser:
                open_window(url)
            threading.Thread(target=idle_watch, daemon=True).start()
            done.wait()
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
        try:
            os.remove(STATE_FILE)
        except OSError:
            pass
    return 0


# -- registering the icm:// link and the .icmjob file type with the operating system ------------------
def _cmd_line(extra: str) -> list:
    return [sys.executable, "-m", "icm", "app", *extra.split()]


LAUNCHER = '''# written by `python -m icm app install`: starts the ICM app from the checkout, without a console window
import os, subprocess, sys
env = dict(os.environ, PYTHONHASHSEED="0")
exe = sys.executable
if exe.lower().endswith("pythonw.exe") and os.path.exists(exe[:-5] + ".exe"):
    exe = exe[:-5] + ".exe"                       # python.exe + CREATE_NO_WINDOW: stdout/stderr work, no window
flags = (0x08000000 | 0x00000008) if os.name == "nt" else 0       # CREATE_NO_WINDOW | DETACHED_PROCESS
subprocess.Popen([exe, "-m", "icm", "app", "open", *sys.argv[1:]], cwd=%r, env=env, creationflags=flags,
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
'''


def write_launcher(here: str) -> str:
    os.makedirs(HOME, exist_ok=True)
    path = os.path.join(HOME, "launch.pyw")
    open(path, "w", encoding="utf8").write(LAUNCHER % here)
    return path


def install() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # the checkout (analysis/)
    sysname = platform.system()
    py = sys.executable
    if sysname == "Darwin":
        app = os.path.expanduser("~/Applications/ICM.app")
        script = f'''on open location u
	do shell script "cd " & quoted form of "{here}" & " && " & quoted form of "{py}" & " -m icm app open " & quoted form of u & " >/dev/null 2>&1 &"
end open location
on open fs
	repeat with f in fs
		do shell script "cd " & quoted form of "{here}" & " && " & quoted form of "{py}" & " -m icm app open " & quoted form of (POSIX path of f) & " >/dev/null 2>&1 &"
	end repeat
end open
on run
	do shell script "cd " & quoted form of "{here}" & " && " & quoted form of "{py}" & " -m icm app open >/dev/null 2>&1 &"
end run
'''
        os.makedirs(os.path.dirname(app), exist_ok=True)
        tmp = os.path.join(HOME, "ICM.applescript")
        os.makedirs(HOME, exist_ok=True)
        open(tmp, "w", encoding="utf8").write(script)
        subprocess.run(["rm", "-rf", app], check=True)
        subprocess.run(["osacompile", "-o", app, tmp], check=True)
        plist = os.path.join(app, "Contents", "Info.plist")
        pb = lambda c: subprocess.run(["/usr/libexec/PlistBuddy", "-c", c, plist], check=True, capture_output=True)
        pb("Add :CFBundleIdentifier string org.quran-hadith-studies.icm")
        pb("Add :LSUIElement bool true")                                         # no Dock icon: the page is the window
        pb("Add :CFBundleURLTypes array")
        pb("Add :CFBundleURLTypes:0 dict")
        pb("Add :CFBundleURLTypes:0:CFBundleURLName string ICM job")
        pb("Add :CFBundleURLTypes:0:CFBundleURLSchemes array")
        pb(f"Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string {SCHEME}")
        try:
            pb("Delete :CFBundleDocumentTypes")
        except subprocess.CalledProcessError:
            pass
        pb("Add :CFBundleDocumentTypes array")
        pb("Add :CFBundleDocumentTypes:0 dict")
        pb("Add :CFBundleDocumentTypes:0:CFBundleTypeName string ICM job")
        pb("Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Viewer")
        pb("Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions array")
        pb(f"Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 string {EXT[1:]}")
        subprocess.run(["codesign", "--force", "--deep", "-s", "-", app], capture_output=True)
        subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister", "-f", app], capture_output=True)
        return f"installed {app}; icm:// links and {EXT} files now open it"
    if sysname == "Linux":
        d = os.path.expanduser("~/.local/share/applications")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "icm.desktop"), "w").write(
            f"[Desktop Entry]\nType=Application\nName=ICM\nExec=sh -c 'cd \"{here}\" && \"{py}\" -m icm app open %u'\nNoDisplay=true\n"
            f"MimeType=x-scheme-handler/{SCHEME};application/x-icmjob;\n")
        mime = os.path.expanduser("~/.local/share/mime/packages")
        os.makedirs(mime, exist_ok=True)
        open(os.path.join(mime, "icmjob.xml"), "w").write(
            f'<?xml version="1.0"?><mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info"><mime-type type="application/x-icmjob">'
            f'<comment>ICM job</comment><glob pattern="*{EXT}"/></mime-type></mime-info>')
        for c in (["update-mime-database", os.path.expanduser("~/.local/share/mime")], ["update-desktop-database", d],
                  ["xdg-mime", "default", "icm.desktop", f"x-scheme-handler/{SCHEME}"], ["xdg-mime", "default", "icm.desktop", "application/x-icmjob"]):
            try:
                subprocess.run(c, capture_output=True)
            except FileNotFoundError:
                pass
        return "installed ~/.local/share/applications/icm.desktop"
    if sysname == "Windows":
        import winreg
        pyw = py[:-4] + "w.exe" if py.lower().endswith("python.exe") and os.path.exists(py[:-4] + "w.exe") else py
        cmd = f'"{pyw}" "{write_launcher(here)}" "%1"'
        for key, label in ((f"Software\\Classes\\{SCHEME}", "URL:ICM job"), (f"Software\\Classes\\icmjobfile", "ICM job")):
            k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key)
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, label)
            if key.endswith(SCHEME):
                winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
            winreg.SetValue(winreg.HKEY_CURRENT_USER, key + "\\shell\\open\\command", winreg.REG_SZ, cmd)
        winreg.SetValue(winreg.HKEY_CURRENT_USER, f"Software\\Classes\\{EXT}", winreg.REG_SZ, "icmjobfile")
        try:                                                   # tell Explorer the associations changed
            import ctypes
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
        except Exception:
            pass
        return "registered icm:// and .icmjob for the current user"
    raise SystemExit(f"unsupported system {sysname}")


def uninstall() -> str:
    sysname = platform.system()
    if sysname == "Darwin":
        subprocess.run(["rm", "-rf", os.path.expanduser("~/Applications/ICM.app")])
    elif sysname == "Linux":
        for f in ("~/.local/share/applications/icm.desktop", "~/.local/share/mime/packages/icmjob.xml"):
            try:
                os.remove(os.path.expanduser(f))
            except OSError:
                pass
    elif sysname == "Windows":
        import winreg
        for key in (f"Software\\Classes\\{SCHEME}\\shell\\open\\command", f"Software\\Classes\\{SCHEME}\\shell\\open", f"Software\\Classes\\{SCHEME}\\shell", f"Software\\Classes\\{SCHEME}",
                    "Software\\Classes\\icmjobfile\\shell\\open\\command", "Software\\Classes\\icmjobfile\\shell\\open", "Software\\Classes\\icmjobfile\\shell", "Software\\Classes\\icmjobfile", f"Software\\Classes\\{EXT}"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass
    return "removed"


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if a and a[0] == "install":
        print(install())
        return 0
    if a and a[0] == "uninstall":
        print(uninstall())
        return 0
    target = a[1] if a and a[0] == "open" and len(a) > 1 else None
    return serve(target)
