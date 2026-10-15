"""Desktop app: link parsing, server allow-list, the local page's guards. No network, no model run."""
import json, os, sys, threading, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http.server import ThreadingHTTPServer
from icm import app


def test_parse_and_allowlist():
    j = app.parse_link("icm://run?server=https%3A%2F%2Febadollah.ir%2Ficm&pid=ab12&token=" + "t" * 24)
    assert j == dict(server="https://ebadollah.ir/icm", pid="ab12", token="t" * 24)
    for bad in ("https://x/", "icm://other?server=a&pid=b&token=c", "icm://run?server=a&pid=b"):
        try:
            app.parse_link(bad); assert False, bad
        except ValueError:
            pass
    assert app.server_allowed("https://ebadollah.ir/icm") and app.server_allowed("http://127.0.0.1:8000/icm")
    assert not app.server_allowed("https://evil.example/icm") and not app.server_allowed("http://ebadollah.ir/icm")


def test_page_needs_secret_and_local_host():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        b = f"http://127.0.0.1:{port}"
        assert app.APP.secret in urllib.request.urlopen(b + "/").read().decode()
        def code(req):
            try:
                return urllib.request.urlopen(req).status
            except urllib.error.HTTPError as e:
                return e.code
        assert code(urllib.request.Request(b + "/state")) == 404                                  # no secret
        assert code(urllib.request.Request(b + "/state", headers={"X-App": app.APP.secret})) == 200
        assert code(urllib.request.Request(b + "/run", data=b"{}", method="POST")) == 403
        assert code(urllib.request.Request(b + "/state", headers={"X-App": app.APP.secret, "Host": "evil.example"})) == 403   # DNS rebinding
    finally:
        srv.shutdown()


if __name__ == "__main__":
    test_parse_and_allowlist(); test_page_needs_secret_and_local_host(); print("ok")
