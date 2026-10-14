"""reel-intel command line.

  reel-intel run SOURCE [--profile HANDLE] [--limit N] [--open] [--data DIR] [--lang pl|en]
                                 links file / grid.tsv / URLs -> scan -> QA -> page (one command)
  reel-intel demo [--out DIR]    open a demo page built from synthetic data (no account, no network)
  reel-intel doctor              check Python, ffmpeg, yt-dlp, GPU, network, write access
  reel-intel insights EXPORT.csv add your own analytics (reach, saves, shares, watch time) to scanned videos
  reel-intel watch SOURCE [--alert 5]   scan what is new, flag videos that pop, append to digest.md (docs/MONITORING.md)
  reel-intel mcp                 MCP server for Claude Desktop / Cursor (also: reel-intel-mcp)
  reel-intel score script.txt    check your own script against what your data shows (clear features, similar videos)
  reel-intel compare FOLDER FOLDER ...   several profiles side by side: what repeats, what is one account's style
  reel-intel serve [--port N]    local app in your browser: paste links, watch progress, read the page
  reel-intel platforms           test which platforms work today (writes docs/PLATFORMS.md)
  reel-intel snippet             print a browser-console snippet that copies a profile's reel links (grid.tsv)
  reel-intel visuals             what the picture and sound show (cuts, face, on-screen text, burned-in captions): not only the transcript
  reel-intel report digest       ONE file for an AI agent: speech, on-screen text, caption and the skills/repos/commands to check (also written by `run`)
  reel-intel shorten             automatic short version (hook, key sentences, CTA, on-screen text); never overwrites one written by a person or Claude
  reel-intel scan|qa|report|page [...]   the individual steps
Data folder: --data DIR, else $REEL_DATA, else ./reel-data.  Interface language: --lang or ui_language in <data>/config.yaml.
"""
import importlib
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from . import __version__

SNIPPET = r"""(async()=>{const h=location.pathname.split('/')[1],host=location.hostname,N=60,m={};let stale=0,prev=0;
const sel=host.includes('tiktok')?'a[href*="/video/"]':host.includes('youtube')?'a[href*="/shorts/"]':'a[href*="/reel/"],a[href*="/videos/"]';
for(let i=0;i<60&&stale<4;i++){document.querySelectorAll(sel).forEach(a=>{m[a.href.split('?')[0]]=a.innerText.split('\n')[0]});
const n=Object.keys(m).length;stale=n===prev?stale+1:0;prev=n;if(n>=N)break;scrollBy(0,2200);await new Promise(r=>setTimeout(r,1400))}
copy('#profile\t'+h+'\n'+Object.entries(m).map(([k,v])=>k+'\t'+v).join('\n'));console.log('Copied '+Object.keys(m).length+' videos. Paste into grid.tsv')})()"""


def pop_opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        v = args[i + 1] if i + 1 < len(args) else default
        del args[i:i + 2]
        return v
    return default


def pop_flag(args, name):
    if name in args:
        args.remove(name)
        return True
    return False


def step(cmd, args):
    """run one reel-intel step in a fresh interpreter (isolates sys.exit, shares the data folder through the environment)"""
    return subprocess.call([sys.executable, "-m", "reel_intel.cli", cmd, *args])


def doctor():
    ok_all = True

    def line(ok, label, fix=""):
        nonlocal ok_all
        ok_all &= ok
        print(f"  {'OK ' if ok else 'FIX'}  {label}" + (f"\n         -> {fix}" if not ok and fix else ""))

    v = sys.version_info
    line((3, 10) <= v[:2] < (3, 13), f"Python {v.major}.{v.minor}.{v.micro} (supported: 3.10-3.12)", "install Python 3.12 and reinstall reel-intel")
    for t in ("ffmpeg", "ffprobe"):
        line(shutil.which(t) is not None, f"{t} on PATH", "Windows: winget install Gyan.FFmpeg | macOS: brew install ffmpeg | Linux: apt install ffmpeg")
    try:
        import yt_dlp
        ver = yt_dlp.version.__version__
        import datetime as dt
        age = (dt.date.today() - dt.datetime.strptime(ver[:10], "%Y.%m.%d").date()).days
        line(age < 90, f"yt-dlp {ver} ({age} days old)", "pip install -U yt-dlp (platforms change often; old versions fail first)")
    except Exception as e:
        line(False, f"yt-dlp ({e})", "pip install yt-dlp")
    try:
        import av
        line(int(av.__version__.split(".")[0]) < 17, f"PyAV {av.__version__}", 'pip install "av>=14,<17"')
        import ctranslate2
        gpu = ctranslate2.get_cuda_device_count() > 0
        line(True, "GPU found: large-v3 model, ~1 s per reel" if gpu else "no GPU: CPU mode (model 'small', several times slower)")
    except Exception as e:
        line(False, f"speech recognition ({e})", "pip install faster-whisper")
    for mod, why in (("cv2", "frames for reels without speech"), ("rapidocr_onnxruntime", "text on screen"), ("yaml", "settings")):
        try:
            importlib.import_module(mod)
            line(True, f"{mod} ({why})")
        except Exception:
            line(False, f"{mod} ({why})", f"pip install {'opencv-python-headless' if mod == 'cv2' else 'rapidocr-onnxruntime' if mod.startswith('rapid') else 'pyyaml'}")
    from .common import CFG, WORK
    try:
        WORK.mkdir(parents=True, exist_ok=True)
        (WORK / ".w").write_text("x")
        (WORK / ".w").unlink()
        line(True, f"data folder writable: {WORK}")
    except Exception as e:
        line(False, f"data folder {WORK} ({e})", "choose another folder with --data DIR")
    if CFG.get("cookies_file"):
        line(Path(CFG["cookies_file"]).exists(), f"cookies file {CFG['cookies_file']}", "export cookies.txt again (see docs) or remove cookies_file")
    try:
        import urllib.request
        urllib.request.urlopen(urllib.request.Request("https://www.instagram.com/", method="HEAD"), timeout=6)
        line(True, "network: instagram.com reachable")
    except Exception as e:
        line(False, f"network ({str(e)[:60]})", "check internet/VPN; platforms may block some networks")
    print("\nAll good." if ok_all else "\nFix the lines marked FIX, then run `reel-intel doctor` again.")
    return 0 if ok_all else 1


def demo(args):
    out = Path(pop_opt(args, "--out", "reel-demo")).resolve()
    src = Path(importlib.import_module("reel_intel").__file__).parent / "demo"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(src, out)
    os.environ["REEL_DATA"] = str(out)
    if (lang := pop_opt(args, "--lang")):
        os.environ["REEL_UI_LANG"] = lang
    rc = step("page", [])
    page = out / "Reele.html"
    print(f"demo page: {page}")
    if rc == 0 and not pop_flag(args, "--no-open"):
        webbrowser.open(page.as_uri())
    return rc


def run_all(args):
    profile = pop_opt(args, "--profile")
    limit = pop_opt(args, "--limit")
    lang = pop_opt(args, "--lang")
    open_page = pop_flag(args, "--open")
    no_visuals = pop_flag(args, "--no-visuals")
    if lang:
        os.environ["REEL_UI_LANG"] = lang
    if not args:
        print(__doc__)
        return 2
    scan_args = list(args)
    if profile:
        scan_args += ["--profile", profile]
    if limit:
        scan_args += ["--limit", limit]
    print("1/5 scan", flush=True)
    rc = step("scan", scan_args)
    if rc:
        return rc
    print("2/5 short versions", flush=True)
    step("shorten", [])
    if not no_visuals:
        print("3/5 picture and sound (what is not in the transcript; --no-visuals skips it)", flush=True)
        step("visuals", [])
        step("report", ["screen"])
    step("report", ["digest"])
    print("4/5 quality check", flush=True)
    step("qa", [])
    print("5/5 page", flush=True)
    rc = step("page", [])
    from .common import WORK
    page = WORK / "Reele.html"
    print(f"\nDone. Page: {page}\nQuality report: {WORK / 'qa_report.md'}")
    if rc == 0 and open_page:
        webbrowser.open(page.as_uri())
    return rc


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    data = pop_opt(args, "--data")
    if data:
        os.environ["REEL_DATA"] = str(Path(data).resolve())
    lang = pop_opt(args, "--lang") if args[:1] != ["run"] and args[:1] != ["demo"] else None
    if lang:
        os.environ["REEL_UI_LANG"] = lang
    cmd = args.pop(0) if args else "help"
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd in ("version", "--version"):
        print(f"reel-intel {__version__}")
        return 0
    if cmd == "snippet":
        print("Open the profile's Reels tab in your logged-in browser, press F12, open the Console, paste this and press Enter.\n"
              "Then save the clipboard as grid.tsv and run:  reel-intel run grid.tsv\n\n" + SNIPPET)
        return 0
    if cmd == "doctor":
        return doctor()
    if cmd == "demo":
        return demo(args)
    if cmd == "run":
        return run_all(args)
    if cmd == "insights":
        sys.argv = ["reel-intel insights", *args]
        importlib.import_module("reel_intel.insights").main()
        return 0
    if cmd == "watch":
        sys.argv = ["reel-intel watch", *args]
        importlib.import_module("reel_intel.watch").main()
        return 0
    if cmd == "mcp":
        importlib.import_module("reel_intel.mcp_server").main()
        return 0
    if cmd == "score":
        sys.argv = ["reel-intel score", *args]
        importlib.import_module("reel_intel.score").main()
        return 0
    if cmd == "compare":
        sys.argv = ["reel-intel compare", *args]
        importlib.import_module("reel_intel.compare").main()
        return 0
    if cmd == "serve":
        sys.argv = ["reel-intel serve", *args]
        importlib.import_module("reel_intel.server").main()
        return 0
    if cmd == "platforms":
        sys.argv = ["reel-intel platforms", *args]
        importlib.import_module("reel_intel.platforms").main()
        return 0
    if cmd in ("scan", "qa", "report", "page", "shorten", "visuals"):
        sys.argv = [f"reel-intel {cmd}", *args]
        importlib.import_module(f"reel_intel.{cmd}").main()
        return 0
    print(f"unknown command: {cmd}\n\n{__doc__}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
