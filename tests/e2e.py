"""Browser test for 404.html (Chromium via Playwright).
Run: python tests/e2e.py                      (uses a small stand-in for the app)
     python tests/e2e.py path/to/OBdating     (uses a real copy of the OBdating site)

A local server stands in for GitHub Pages:
- addresses are case-sensitive
- /OBdating/ is a project site, and /OBdating without the slash answers 301
- every address that does not exist answers 404 with the body of 404.html
- /OBdating/?slow=1 waits 4 seconds before it answers (a slow network)
"""
import http.server
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
NOT_FOUND = (ROOT / "404.html").read_bytes()
SITE = "OBdating"

if len(sys.argv) > 1:
    APP = Path(sys.argv[1]).resolve()
    if not (APP / "index.html").is_file():
        sys.exit(f"no index.html in {APP}")
else:
    APP = Path(tempfile.mkdtemp(prefix="usersite-e2e-"))
    (APP / "index.html").write_text(
        "<!doctype html><html lang='th'><head><meta charset='utf-8'>"
        "<title>OBdating stand-in</title></head><body><h1 id='app'>app</h1></body></html>",
        encoding="utf-8",
    )

index_text = (APP / "index.html").read_text(encoding="utf-8")
APP_TITLE = index_text.split("<title>", 1)[1].split("</title>", 1)[0]

TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript",
         ".md": "text/markdown; charset=utf-8", ".woff2": "font/woff2", ".json": "application/json"}

passed, failed = 0, 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL {name} {detail}")


class PagesLike(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, body, ctype="text/html; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path
        if path == f"/{SITE}":
            target = f"/{SITE}/" + (f"?{parts.query}" if parts.query else "")
            return self.send(301, b"", extra={"Location": target})
        if path.startswith(f"/{SITE}/"):
            if "slow=1" in parts.query:
                time.sleep(4)
            rel = path[len(SITE) + 2:]
            if rel == "" or rel.endswith("/"):
                rel += "index.html"
            file = (APP / rel).resolve()
            if APP in file.parents and file.is_file():
                return self.send(200, file.read_bytes(), TYPES.get(file.suffix, "application/octet-stream"))
            return self.send(404, NOT_FOUND)
        if path == "/404.html":
            return self.send(200, NOT_FOUND)
        return self.send(404, NOT_FOUND)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), PagesLike)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"


def is_app(page):
    return page.title() == APP_TITLE and page.locator("#notfound").count() == 0


def shown(page, sel):
    return page.locator(sel).evaluate("el => getComputedStyle(el).display") != "none"


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(locale="th-TH")
    page = ctx.new_page()

    requests = []
    page.on("request", lambda r: requests.append(r.url))
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    # ---------- 1. wrong upper/lower case goes to the app ----------
    for typed in ["/obdating", "/obdating/", "/OBDATING", "/OBDATING/", "/Obdating", "/oBdAtInG/", "/OBDating"]:
        first = page.goto(BASE + typed, wait_until="commit")
        check(f"{typed}: the server itself says 404", first.status == 404, first.status)
        page.wait_for_url(f"{BASE}/{SITE}/", wait_until="load")
        check(f"{typed}: lands on /{SITE}/", page.url == f"{BASE}/{SITE}/", page.url)
        check(f"{typed}: the app is what opens", is_app(page), page.title())

    # ---------- 2. the rest of the address is kept ----------
    page.goto(BASE + "/obdating?v=test#rules", wait_until="commit")
    page.wait_for_url(f"{BASE}/{SITE}/?v=test#rules", wait_until="load")
    check("query and hash kept", page.url == f"{BASE}/{SITE}/?v=test#rules" and is_app(page), page.url)

    page.goto(BASE + "/OBDATING/index.html", wait_until="commit")
    page.wait_for_url(f"{BASE}/{SITE}/index.html", wait_until="load")
    check("file name kept", page.url == f"{BASE}/{SITE}/index.html" and is_app(page), page.url)

    # ---------- 3. the correct address is untouched ----------
    resp = page.goto(f"{BASE}/{SITE}/", wait_until="load")
    check("correct address answers 200 directly", resp.status == 200 and is_app(page), resp.status)
    resp = page.goto(f"{BASE}/{SITE}", wait_until="load")
    check("correct address without the slash", page.url == f"{BASE}/{SITE}/" and is_app(page), page.url)

    # ---------- 4. never a loop, never a redirect for anything else ----------
    for typed in [f"/{SITE}/missing.html", "/", "/obdatin", "/obdating2", "/something/obdating", "/404.html"]:
        visits = []
        handler = lambda frame: visits.append(frame.url) if frame == page.main_frame else None
        page.on("framenavigated", handler)
        resp = page.goto(BASE + typed, wait_until="load")
        page.wait_for_timeout(2000)  # longer than the 1.5 s timer in the page
        page.remove_listener("framenavigated", handler)
        check(f"{typed}: stays on the same address", page.url == BASE + typed, page.url)
        check(f"{typed}: loaded exactly once", len(visits) == 1, visits)
        check(f"{typed}: shows the not-found text", shown(page, "#notfound") and not shown(page, "#moving"))
        check(f"{typed}: title", page.title() == "ไม่พบหน้านี้", page.title())

    # ---------- 5. the back button does not return to the wrong address ----------
    page.goto("about:blank")
    page.goto(BASE + "/obdating", wait_until="commit")
    page.wait_for_url(f"{BASE}/{SITE}/", wait_until="load")
    page.go_back()
    check("back goes to the page before, not to the wrong address", page.url == "about:blank", page.url)

    # ---------- 6. slow network: a message with a link, never the not-found text ----------
    # Playwright cannot run commands in a page while its next page is still on the way,
    # so the page reports its own state through console messages every 250 ms.
    states = []

    def on_console(msg):
        if msg.text.startswith("STATE "):
            states.append(json.loads(msg.text[6:]))

    page.on("console", on_console)
    page.add_init_script("""
    (() => {
      const t0 = performance.now();
      const display = (id) => { const el = document.getElementById(id); return el ? getComputedStyle(el).display : null; };
      setInterval(() => {
        const go = document.getElementById('go');
        console.log('STATE ' + JSON.stringify({
          ms: Math.round(performance.now() - t0), path: location.pathname,
          nf: display('notfound'), mv: display('moving'), href: go ? go.getAttribute('href') : null,
        }));
      }, 250);
    })();
    """)
    want = f"/{SITE}/?slow=1#rules"
    page.goto(BASE + "/OBDATING?slow=1#rules", wait_until="commit")
    page.wait_for_url(BASE + want, wait_until="load")
    waiting = [s for s in states if s["path"] == "/OBDATING"]
    early = [s for s in waiting if s["ms"] < 1400]
    late = [s for s in waiting if s["ms"] > 1700]
    check("slow: the page was watched while it waited", len(early) >= 3 and len(late) >= 3, (len(early), len(late)))
    check("slow: nothing shown at first", all(s["nf"] == "none" and s["mv"] == "none" for s in early), early)
    check("slow: message appears after 1.5 s", all(s["mv"] == "block" for s in late), late)
    check("slow: not-found text never shown", all(s["nf"] == "none" for s in waiting))
    check("slow: manual link points to the app", all(s["href"] == want for s in waiting), waiting[:1])
    check("slow: arrives once the server answers", page.url == BASE + want and is_app(page), page.url)
    page.remove_listener("console", on_console)

    # ---------- 7. nothing is loaded from another website by 404.html ----------
    outside = [u for u in requests if not u.startswith(BASE + "/") and not u.startswith(("data:", "blob:", "about:"))]
    check("no request leaves this website", outside == [], outside[:5])
    check("no script error", errors == [], errors[:3])
    ctx.close()

    # ---------- 8. JavaScript switched off: plain not-found page, no redirect ----------
    ctx = browser.new_context(java_script_enabled=False)
    page = ctx.new_page()
    resp = page.goto(BASE + "/obdating", wait_until="load")
    check("no JS: stays", page.url == BASE + "/obdating" and resp.status == 404, page.url)
    check("no JS: shows the not-found text", shown(page, "#notfound") and not shown(page, "#moving"))
    ctx.close()

    # ---------- 9. phone-sized screen ----------
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(BASE + "/obdating", wait_until="commit")
    page.wait_for_url(f"{BASE}/{SITE}/", wait_until="load")
    check("phone: lands on the app", is_app(page), page.url)
    page.goto(BASE + "/nothing-here", wait_until="load")
    box = page.locator("#notfound").bounding_box()
    check("phone: not-found text fits the screen", box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 390, box)
    ctx.close()
    browser.close()

srv.shutdown()
print(f"{passed} passed, {failed} failed (app title: {APP_TITLE})")
sys.exit(0 if failed == 0 else 1)
