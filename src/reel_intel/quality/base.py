"""base.py - what every detector shares: Finding (one result), Ctx (everything a detector may look at), the registry of fault codes.

A detector is a plain function  check(ctx: Ctx) -> list[Finding]  that only does arithmetic on the arrays and numbers in ctx. It never renders,
never starts a model on its own (a model's output arrives in ctx.ears etc.), never reads a file except through ctx. That keeps every detector testable
with synthetic sound and keeps CI free of GPUs. A module lists its detectors in  DETECTORS = [check_a, check_b, ...].
"""
from dataclasses import dataclass, field
from pathlib import Path

SEVERITIES = ("ERROR", "WARN", "INFO")
REGISTRY = {}                                   # code -> {"desc", "sev", "repair", "module"}


def register(code, desc, sev="WARN", repair=None, module=""):
    """declare a fault code; `repair` names the action the repair loop takes (see quality/repair.py), None when only a human can fix it"""
    REGISTRY[code] = {"desc": desc, "sev": sev, "repair": repair, "module": module}
    return code


@dataclass
class Finding:
    sev: str
    code: str
    msg: str
    scene: int = None                           # 1-based scene number, when the fault belongs to one
    t: float = None                             # seconds into the reel
    data: dict = field(default_factory=dict)    # numbers behind the message (used by repair and by tests)

    def as_tuple(self):
        return (self.sev, self.code, self.msg)


def finding(code, msg, sev=None, scene=None, t=None, **data):
    """a Finding for a registered code (severity defaults to the registered one)"""
    if code not in REGISTRY:
        raise KeyError(f"unregistered QA code {code}")
    return Finding(sev or REGISTRY[code]["sev"], code, msg, scene, t, data)


@dataclass
class Ctx:
    side: dict                                  # the sidecar <reel>.qa.json (scenes with words/start/end/heard/missed/tail, engine, params...)
    final: object = None                        # numpy float32 mono, the final mix as heard (decoded from the mp4)
    sr: int = 24000
    voice: object = None                        # the dry voice track, same length (None when unavailable)
    music: object = None                        # the music stem as mixed (already scaled), same length (None when unavailable)
    profile: dict = field(default_factory=dict)
    mp4: Path = None
    ears: dict = None                           # output of ears.py (windows with labels), or None
    frames: object = None                       # callable(t_seconds) -> HxWx3 uint8 RGB frame of the video, or None
    fps: int = 30

    @property
    def scenes(self):
        return self.side.get("scenes", [])
