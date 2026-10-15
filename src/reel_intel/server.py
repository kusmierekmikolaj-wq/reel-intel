"""server.py - local app: `reel-intel serve` opens a small page in your browser (form -> progress -> result page).

Safety: listens on 127.0.0.1 only; every POST needs the random token embedded in the page and a localhost Host header; links are
validated (http/https only) and passed to the scan as a file, never to a shell; one job at a time.
"""
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

from .common import CODE, DATA, WORK

LINK_RE = re.compile(r"^https?://[^\s\"'<>]+$")
MAX_SOURCES = 200
JOB = {"state": "idle", "log": [], "started": None, "ended": None, "dir": None}
LOCK = threading.Lock()
TOKEN = secrets.token_urlsafe(18)
LIBRARY = None                       # folder whose sub-folders are finished analyses (each with Reele.html); set by --library or $REEL_LIBRARY


def library():
    """[{id, title, reels}] for every sub-folder of LIBRARY that has a page, plus the account comparison when COMPARE.md is there.
    labels.json in LIBRARY ({"folder": "label"}) replaces the page's own title."""
    out_scans = []
    if WORK.is_dir():
        for d in sorted(WORK.iterdir(), key=lambda x: -x.stat().st_mtime):
            if d.is_dir() and (d / "Reele.html").exists():
                n = sum(1 for _ in (d / "data").glob("*/features.json")) if (d / "data").exists() else 0
                out_scans.append({"id": "scan:" + d.name, "title": "@" + d.name.lstrip("@"), "reels": n, "user": True})
    if not LIBRARY or not LIBRARY.is_dir():
        return out_scans
    labels = {}
    if (LIBRARY / "labels.json").exists():
        try:
            labels = json.loads((LIBRARY / "labels.json").read_text(encoding="utf-8"))
        except Exception:
            labels = {}
    out = []
    for d in sorted(LIBRARY.iterdir()):
        if d.is_dir() and d.resolve() != WORK.resolve() and (d / "Reele.html").exists():
            m = re.search(r"<title>(.*?)</title>", (d / "Reele.html").read_text(encoding="utf-8", errors="replace")[:4000], re.S)
            n = sum(1 for x in (d / "data").glob("*/features.json")) if (d / "data").exists() else 0
            lab = labels.get(d.name)
            item = {"id": d.name, "title": (lab if isinstance(lab, str) else (lab or {}).get("name")) or (m.group(1).strip() if m else d.name), "reels": n}
            if isinstance(lab, dict):
                item.update({k: str(lab[k]) for k in ("letter", "name", "about", "platform") if lab.get(k)})
            out.append(item)
    if (LIBRARY / "COMPARE.md").exists():
        lab = labels.get("_compare", "Account comparison")
        out.append({"id": "_compare", "title": lab if isinstance(lab, str) else lab.get("name", "Account comparison"), "reels": 0,
                    **({k: str(lab[k]) for k in ("about",) if lab.get(k)} if isinstance(lab, dict) else {})})
    order = list(labels)                                                    # the order of labels.json, then anything unlisted
    out.sort(key=lambda x: order.index(x["id"]) if x["id"] in order else len(order))
    return out + out_scans


def markdown_page(md):
    """COMPARE.md as a readable page: headings, tables, bullets; everything else as paragraphs. No external libraries."""
    import html
    body, rows = [], []

    def flush():
        if rows:
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows if not re.fullmatch(r"[\s|:-]+", r)]
            body.append("<table>" + "".join(f"<tr>{''.join(('<th>' if i == 0 else '<td>') + html.escape(c).replace('**', '') + ('</th>' if i == 0 else '</td>') for c in r)}</tr>" for i, r in enumerate(cells)) + "</table>")
            rows.clear()
    for line in md.splitlines():
        if line.startswith("|"):
            rows.append(line)
            continue
        flush()
        if line.startswith("# "):
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("- "):
            body.append(f"<p>• {html.escape(line[2:]).replace('**', '')}</p>")
        elif line.strip():
            body.append(f"<p>{html.escape(line)}</p>")
    flush()
    title = next((l[2:] for l in md.splitlines() if l.startswith("# ")), "reel-intel")
    css = ("body{font:16px/1.5 system-ui,Segoe UI,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#12161C;background:#F4F6F9}"
           "table{border-collapse:collapse;width:100%;margin:12px 0;background:#fff}th,td{border:1px solid #D9DFE8;padding:6px 10px;text-align:left;font-size:.92rem}"
           "th{background:#E8ECF4}h1{font-size:1.6rem}h2{margin-top:28px}")
    return f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>{css}</style>{''.join(body)}"


def parse_sources(text):
    """textarea -> (kind, lines). Lines with a TAB come from `reel-intel snippet` (grid.tsv); otherwise one link per line. Invalid lines are dropped."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    if any("\t" in l for l in lines):
        keep = [l for l in lines if l.startswith("#profile") or (l.split("\t")[0].startswith("http") and LINK_RE.match(l.split("\t")[0]))]
        return "grid", keep[:MAX_SOURCES + 1]
    return "urls", [l.strip() for l in lines if LINK_RE.match(l.strip())][:MAX_SOURCES]


def job_dir(lines):
    """every scan gets its own folder, named after the profile (from the '#profile' line) or after the time: different accounts never mix"""
    name = None
    for l in lines:
        if l.startswith("#profile") and "\t" in l:
            name = re.sub(r"[^A-Za-z0-9_.-]", "", l.split("\t")[1])[:40]
    return WORK / (name or time.strftime("skan-%Y%m%d-%H%M"))


def start_job(text, lang, limit):
    kind, lines = parse_sources(text)
    if not [l for l in lines if not l.startswith("#")]:
        return False, "no valid links"
    with LOCK:
        if JOB["state"] == "running":
            return False, "a job is already running"
        JOB.update(state="running", log=[], started=time.time(), ended=None)
    jd = job_dir(lines)
    jd.mkdir(parents=True, exist_ok=True)
    JOB["dir"] = jd
    src = jd / ("grid.tsv" if kind == "grid" else "urls.txt")
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [sys.executable, "-m", "reel_intel.cli", "run", str(src), "--lang", lang if lang in ("pl", "en") else "pl", "--limit", str(max(1, min(int(limit), 200)))]

    def work():
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                 env={**os.environ, "REEL_DATA": str(jd)})
            for line in p.stdout:
                JOB["log"].append(line.rstrip())
                del JOB["log"][:-400]
            JOB["state"] = "done" if p.wait() == 0 else "error"
        except Exception as e:                                                   # never leave the UI waiting forever
            JOB["log"].append(f"error: {e}")
            JOB["state"] = "error"
        JOB["ended"] = time.time()
    threading.Thread(target=work, daemon=True).start()
    return True, "started"


MAKE = {"state": "idle", "log": "", "video": None, "account": None, "text": ""}
SLUG = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")


def make_base(account):
    """folder of an analysed account; only ones the listing offers (no path tricks)"""
    if account not in {x["id"] for x in library()} or account == "_compare":
        return None
    return WORK / account[5:] if account.startswith("scan:") else (LIBRARY / account if LIBRARY else None)


def start_make(account, action, script_text, name, lang):
    base = make_base(account)
    if not base or action not in ("brief", "check", "render"):
        return False, "unknown account or action"
    slug = name if SLUG.match(name or "") else "rolka"
    rd = base / "rolki"
    path = rd / f"{slug}.json"
    if action != "brief":
        try:
            json.loads(script_text)
        except Exception:
            return False, "the script is not valid JSON"
    with LOCK:
        if MAKE["state"] == "running":
            return False, "a reel is being made"
        MAKE.update(state="running", log="", video=None, account=account, text="")
    rd.mkdir(exist_ok=True)
    if action != "brief":
        path.write_text(script_text, encoding="utf-8")
    cmd = [sys.executable, "-m", "reel_intel.cli", "make", action] + ([str(path)] if action != "brief" else [])

    def work():
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                               env={**os.environ, "REEL_DATA": str(base), "REEL_UI_LANG": lang if lang in ("pl", "en") else "pl"})
            MAKE["log"] = (p.stdout + p.stderr).strip()[-1500:]
            if action == "render" and p.returncode == 0 and (rd / f"{slug}.mp4").exists():
                MAKE["video"] = f"/rolki/{quote(account)}/{slug}.mp4"
            if action == "brief" and p.returncode == 0:
                f = next((base / n for n in ("BRIEF_ROLKI.md", "REEL_BRIEF.md") if (base / n).exists()), None)
                MAKE["text"] = f.read_text(encoding="utf-8") if f else ""
            MAKE["state"] = "done" if p.returncode == 0 else "error"
        except Exception as e:
            MAKE["log"] = f"error: {e}"
            MAKE["state"] = "error"
    threading.Thread(target=work, daemon=True).start()
    return True, "started"


def state():
    base = JOB["dir"] or WORK
    n = sum(1 for d in (base / "data").iterdir() if (d / "features.json").exists()) if (base / "data").exists() else 0
    return {"state": JOB["state"], "log": JOB["log"][-60:], "reels": n, "page": (base / "Reele.html").exists(),
            "elapsed": round((JOB["ended"] or time.time()) - JOB["started"]) if JOB["started"] else 0}


def grid_text(profile, items):
    """what the browser extension collected -> the same grid.tsv text that `reel-intel snippet` produces ('#profile<TAB>name', then 'url<TAB>views')"""
    name = re.sub(r"[^A-Za-z0-9_.-]", "", str(profile))[:60] or "profile"
    rows = ["#profile\t" + name]
    for it in items[:MAX_SOURCES]:
        url = str(it.get("url", "")).strip()
        if LINK_RE.match(url):
            views = re.sub(r"[\t\r\n]", " ", str(it.get("views", "")))[:20]
            rows.append(url + "\t" + views)
    return "\n".join(rows)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_file(self, path, ctype):
        """a file with Range support (a video must be seekable)"""
        size = path.stat().st_size
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        start, end, code = 0, size - 1, 200
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            end, code = min(end, size - 1), 206
            if start > end:
                return self._send(416, "range not satisfiable")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        with open(path, "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self.headers.get("Origin", "")
        if origin.startswith(("chrome-extension://", "moz-extension://")):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(data)

    def _local(self):
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self._local():
            return self._send(403, "forbidden")
        if self.path == "/":
            html = (CODE / "app.html").read_text(encoding="utf-8").replace("{{TOKEN}}", TOKEN)
            return self._send(200, html)
        if self.path == "/favicon.ico":
            return self._send(200, (CODE / "favicon.png").read_bytes(), "image/png")
        if self.path == "/logo.svg":
            return self._send(200, (CODE / "logo.svg").read_bytes(), "image/svg+xml")
        if self.path == "/api/ping":                                              # lets the browser extension find the app on its port
            return self._send(200, json.dumps({"app": "reel-intel"}), "application/json")
        if self.path == "/api/state":
            return self._send(200, json.dumps(state()), "application/json")
        if self.path == "/api/snippet":
            from .cli import SNIPPET
            return self._send(200, SNIPPET, "text/plain; charset=utf-8")
        if self.path == "/api/library":
            return self._send(200, json.dumps(library(), ensure_ascii=False), "application/json")
        if self.path.startswith("/lib/"):
            lid = unquote(self.path[5:].split("?")[0])
            if lid not in {x["id"] for x in library()}:                                # only what the listing offers: no path tricks
                return self._send(404, "not found")
            if lid == "_compare":
                return self._send(200, markdown_page((LIBRARY / "COMPARE.md").read_text(encoding="utf-8")))
            base = WORK / lid[5:] if lid.startswith("scan:") else LIBRARY / lid
            return self._send(200, (base / "Reele.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/make/state":
            return self._send(200, json.dumps({k: MAKE[k] for k in ("state", "log", "video", "text")}, ensure_ascii=False), "application/json")
        if self.path.startswith("/rolki/"):                                       # /rolki/<account>/<file>: only files of a listed account's rolki folder
            parts = self.path.split("?")[0].split("/")
            base = make_base(unquote(parts[2])) if len(parts) == 4 else None
            name = unquote(parts[3]) if len(parts) == 4 else ""
            types = {".mp4": "video/mp4", ".txt": "text/plain; charset=utf-8", ".json": "application/json"}
            ext = Path(name).suffix.lower()
            f = base / "rolki" / name if base and SLUG.match(name) and ext in types else None
            return self._send_file(f, types[ext]) if f and f.is_file() else self._send(404, "not found")
        if self.path == "/digest":                                                 # one file for an AI agent: speech, screen text, caption, names to check
            base = JOB["dir"] or WORK
            f = next((base / n for n in ("DLA_AI.md", "FOR_AI.md") if (base / n).exists()), None)
            return self._send(200, f.read_text(encoding="utf-8"), "text/plain; charset=utf-8") if f else self._send(404, "no digest yet")
        if self.path.startswith("/screen"):                                       # what was written on the screens: /screen (this scan) or /screen?lib=<id>
            q = unquote(self.path.partition("?lib=")[2])
            if not q:
                base = JOB["dir"] or WORK
            elif q.startswith("scan:"):
                base = WORK / q[5:]
            else:
                base = LIBRARY / q if LIBRARY and q in {x["id"] for x in library()} else None
            f = next((base / n for n in ("NA_EKRANIE.md", "ON_SCREEN.md") if base and (base / n).exists()), None)
            return self._send(200, markdown_page(f.read_text(encoding="utf-8"))) if f else self._send(404, "no screen report yet")
        if self.path.startswith("/page"):
            f = (JOB["dir"] or WORK) / "Reele.html"
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8") if f.exists() else self._send(404, "no page yet")
        self._send(404, "not found")

    def _from_extension(self):
        """a browser extension's request: the browser itself sets an extension Origin (a web page cannot forge it) and we also require a custom
        header, which a plain cross-site form or image request cannot add"""
        return self.headers.get("Origin", "").startswith(("chrome-extension://", "moz-extension://")) and self.headers.get("X-Reel-Intel-Client") == "extension"

    def do_OPTIONS(self):
        """CORS preflight, answered only for browser extensions (never for web pages)"""
        origin = self.headers.get("Origin", "")
        if not self._local() or not origin.startswith(("chrome-extension://", "moz-extension://")):
            return self._send(403, "forbidden")
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Reel-Intel-Client")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if self.path == "/api/ingest":
            if not self._local() or not self._from_extension():
                return self._send(403, json.dumps({"ok": False, "msg": "forbidden"}), "application/json")
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 2_000_000)) or b"{}")
                ok, msg = start_job(grid_text(body.get("profile", ""), body.get("items") or []), str(body.get("lang", "pl")), int(body.get("limit", 30)))
            except Exception as e:
                ok, msg = False, f"bad request: {e}"
            return self._send(200 if ok else 400, json.dumps({"ok": ok, "msg": msg}), "application/json")
        if not self._local() or self.headers.get("X-Token") != TOKEN:
            return self._send(403, json.dumps({"ok": False, "msg": "forbidden"}), "application/json")
        if self.path == "/api/make":
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 200_000)) or b"{}")
                ok, msg = start_make(str(body.get("account", "")), str(body.get("action", "")), str(body.get("script", "")), str(body.get("name", "rolka")), str(body.get("lang", "pl")))
            except Exception as e:
                ok, msg = False, f"bad request: {e}"
            return self._send(200 if ok else 400, json.dumps({"ok": ok, "msg": msg}), "application/json")
        if self.path != "/api/run":
            return self._send(404, "not found")
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 2_000_000)) or b"{}")
            ok, msg = start_job(str(body.get("text", "")), str(body.get("lang", "pl")), int(body.get("limit", 30)))
        except Exception as e:
            ok, msg = False, f"bad request: {e}"
        self._send(200 if ok else 400, json.dumps({"ok": ok, "msg": msg}), "application/json")


def make_server(port=8765):
    for p in range(port, port + 10):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), Handler)
        except OSError:
            continue
    raise OSError("no free port near %d" % port)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    global LIBRARY
    port = int(args[args.index("--port") + 1]) if "--port" in args else 8765
    lib = args[args.index("--library") + 1] if "--library" in args else os.environ.get("REEL_LIBRARY")
    LIBRARY = Path(lib).expanduser().resolve() if lib else None
    srv = make_server(port)
    url = f"http://127.0.0.1:{srv.server_port}/"
    print(f"reel-intel app: {url}   (data folder: {WORK}{', library: ' + str(LIBRARY) if LIBRARY else ''})   Ctrl+C to stop", flush=True)
    if "--no-open" not in args:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
