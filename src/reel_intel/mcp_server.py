"""mcp_server.py - reel-intel as an MCP server (stdio), so Claude Desktop, Cursor and other MCP clients can call it.

  reel-intel-mcp                      # or: reel-intel mcp
Claude Desktop config:  {"mcpServers": {"reel-intel": {"command": "reel-intel-mcp"}}}
Standard library only (newline-delimited JSON-RPC 2.0). Tools run the normal CLI in a subprocess; `data_dir` selects the data folder.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LINK = re.compile(r"^https?://[^\s\"'<>]+$")
DATA_DIR = {"type": "string", "description": "data folder (default: ./reel-data)"}
TOOLS = [
    {"name": "scan", "description": "Scan public video links (Instagram, TikTok, YouTube Shorts, X, Facebook): exact transcript, caption, numbers; then quality check and page.",
     "inputSchema": {"type": "object", "properties": {"links": {"type": "array", "items": {"type": "string"}, "description": "video links or a YouTube channel link"},
                                                       "limit": {"type": "integer", "default": 20}, "lang": {"type": "string", "enum": ["pl", "en"], "default": "en"}, "data_dir": DATA_DIR}, "required": ["links"]}},
    {"name": "report", "description": "Numbers from a data folder: groups (topics.csv), timing, hooks, features (what goes with views, 99% intervals), texts (verbatim texts + short versions), content (rewrite content_full.md), csv.",
     "inputSchema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["groups", "timing", "hooks", "features", "texts", "content", "csv"]}, "data_dir": DATA_DIR}, "required": ["kind"]}},
    {"name": "page", "description": "Build the readable, searchable page (Reele.html) from a data folder and return its path.",
     "inputSchema": {"type": "object", "properties": {"lang": {"type": "string", "enum": ["pl", "en"], "default": "en"}, "data_dir": DATA_DIR}}},
    {"name": "score", "description": "Check a draft script against what the data shows (clear features, similar videos).",
     "inputSchema": {"type": "object", "properties": {"script": {"type": "string"}, "caption": {"type": "string"}, "data_dir": DATA_DIR}, "required": ["script"]}},
    {"name": "shorten", "description": "Write an automatic short version (hook, key sentences, call to action, on-screen text) for every video that has none. Never overwrites a hand-written one.",
     "inputSchema": {"type": "object", "properties": {"data_dir": DATA_DIR}}},
    {"name": "compare", "description": "Compare several data folders (profiles): what repeats, what is one account's style.",
     "inputSchema": {"type": "object", "properties": {"folders": {"type": "array", "items": {"type": "string"}}}, "required": ["folders"]}},
]


def cli(args, data_dir=None, timeout=3600):
    env = {**os.environ}
    if data_dir:
        env["REEL_DATA"] = str(Path(data_dir).expanduser().resolve())
    r = subprocess.run([sys.executable, "-m", "reel_intel.cli", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)
    return (r.stdout + ("\n" + r.stderr if r.returncode else "")).strip(), r.returncode != 0


def call(name, a):
    d = a.get("data_dir")
    spec = next((x for x in TOOLS if x["name"] == name), None)
    missing = [k for k in (spec or {}).get("inputSchema", {}).get("required", []) if not a.get(k)]
    if missing:
        return f"missing argument: {', '.join(missing)} (see the tool's input schema)", True
    if name == "scan":
        links = [l for l in a.get("links", []) if isinstance(l, str) and LINK.match(l)]
        if not links:
            return "no valid links", True
        src = Path(d or "reel-data").expanduser().resolve()
        src.mkdir(parents=True, exist_ok=True)
        (src / "urls_mcp.txt").write_text("\n".join(links) + "\n", encoding="utf-8")
        out, err = cli(["run", str(src / "urls_mcp.txt"), "--limit", str(min(int(a.get("limit", 20)), 100)), "--lang", a.get("lang", "en")], d)
        return out[-6000:], err
    if name == "report":
        return cli(["report", a["kind"]], d)
    if name == "page":
        out, err = cli(["page"], d) if not a.get("lang") else cli(["--lang", a["lang"], "page"], d)
        return out, err
    if name == "score":
        tmp = Path(d or "reel-data").expanduser().resolve()
        tmp.mkdir(parents=True, exist_ok=True)
        f = tmp / "script_mcp.txt"
        f.write_text(a["script"], encoding="utf-8")
        return cli(["score", str(f)] + (["--caption", a["caption"]] if a.get("caption") else []), d)
    if name == "shorten":
        return cli(["shorten"], d)
    if name == "compare":
        return cli(["compare", *a["folders"], "--out", str(Path(a["folders"][0]).expanduser().resolve() / "COMPARE.md")])
    return f"unknown tool {name}", True


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
                                                          "capabilities": {"tools": {}}, "serverInfo": {"name": "reel-intel", "version": "0.4.1"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        p = msg.get("params", {})
        try:
            text, is_err = call(p.get("name"), p.get("arguments") or {})
        except Exception as e:
            text, is_err = f"error: {e}", True
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text or "(no output)"}], "isError": bool(is_err)}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if mid is not None:                                       # unknown request; notifications (no id) get no reply
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = handle(json.loads(line))
        except Exception as e:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
