"""reel.py - launcher used by the Claude plugin (standard library only).

Runs `reel-intel` from the plugin folder: if the package is not installed yet it creates one shared environment
(`$REEL_VENV`, else ~/.reel-intel/venv) and installs the plugin folder into it. Everything else is the normal CLI:

  python reel.py setup | doctor | demo | run ... | scan ... | qa | report ... | page
"""
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent                       # the plugin folder = repository root (has pyproject.toml)
VENV = Path(os.environ["REEL_VENV"]) if os.environ.get("REEL_VENV") else Path.home() / ".reel-intel" / "venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installed(py):
    return subprocess.run([str(py), "-c", "import reel_intel"], capture_output=True).returncode == 0


def setup(force=False):
    if not PY.exists():
        print(f"creating environment {VENV} ...", flush=True)
        VENV.parent.mkdir(parents=True, exist_ok=True)
        venv.create(VENV, with_pip=True)
    if force or not installed(PY):
        subprocess.check_call([str(PY), "-m", "pip", "install", "-q", "--upgrade", "pip"])
        subprocess.check_call([str(PY), "-m", "pip", "install", "-q", str(ROOT)])
    print(f"ready: {VENV}")


def main():
    args = sys.argv[1:]
    if args[:1] == ["setup"]:
        return setup(force=True)
    py = Path(sys.executable) if installed(sys.executable) else None
    if py is None:
        setup()
        py = PY
    return subprocess.call([str(py), "-m", "reel_intel.cli", *args])


if __name__ == "__main__":
    sys.exit(main())
