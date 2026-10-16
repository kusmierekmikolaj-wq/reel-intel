"""repair.py - from findings to repairs, and the loop  check -> plan -> repair -> check again  (at most 3 rounds).

This module decides WHAT to repair and in which order; it never records, mixes or renders anything itself. The caller passes two callables:
    check()            -> [Finding]    (run the detectors on the current reel)
    apply(action)      -> bool         (change the reel's recipe: a new take, a slower sentence, a different mix ...; True when it did something)
    rebuild(plan)      -> None         (optional: one render / one remix after all actions of a round; plan.needs_render says which)
so the whole loop is tested with fakes and no GPU.

The actions (what each means to the caller, in the order they are applied):
    respell       the words of a scene that were not heard as written get another spelling for the voice (pronounce.candidates), then a new take
    retake_scene  a new recording of the sentence (new seeds), the best heard take is picked again
    slow_scene    the sentence is slowed by the exact factor its speaking rate asks for (atempo, the pitch stays)
    retrim_scene  the end of the sentence is cut harder (the breathy tail, a dragged vowel)
    level_scenes  every scene's voice is brought to the same level before the mix
    remix         the music is mixed again (lower and steadier, filtered)                          audio only: no new render of the picture
    master        the finished loudness is set to the profile's target with a true-peak limiter    audio only
    relayout      the picture's layout is recomputed (slogan, caption, bar) and rendered again
    rerender      the same recipe is rendered again (a failed encode, a black stretch)
A repair is never tried twice for the same finding: after a failed try the plan escalates (retrim -> retake), and when the ladder is used up the finding is
handed to a person (plan.manual) instead of looping. A round that makes the reel worse is undone when the caller can snapshot and restore it.
"""
from dataclasses import dataclass, field

from .base import REGISTRY

ORDER = ("respell", "retake_scene", "slow_scene", "retrim_scene", "level_scenes", "remix", "master", "relayout", "rerender")
TIMING = frozenset({"respell", "retake_scene", "slow_scene", "retrim_scene"})       # these change when words are spoken: the picture must be drawn again
AUDIO_ONLY = frozenset({"level_scenes", "remix", "master"})
PICTURE = frozenset({"relayout", "rerender"})
PER_SCENE = frozenset({"respell", "retake_scene", "slow_scene", "retrim_scene"})
MAX_RETAKES = 2                     # a scene is recorded again at most this many times for one fault
# after a repair has been tried for a finding without success, the next one on its ladder
LADDER = {"respell": ["respell", "retake_scene"], "retrim_scene": ["retrim_scene", "retake_scene"], "slow_scene": ["slow_scene", "retake_scene"],
          "retake_scene": ["retake_scene"] * MAX_RETAKES, "level_scenes": ["level_scenes"], "remix": ["remix", "master"], "master": ["master"],
          "relayout": ["relayout", "rerender"], "rerender": ["rerender"]}


# what a person is left with when the ladder of a finding is used up, by the last repair that was tried
HINT = {None: "a person has to listen, or change the script",
        "retake_scene": "a person has to listen to the sentence, or change its wording",
        "respell": "a person has to listen, or give the word a spelling for the voice in the script's \"say\"",
        "master": "the loudness target is out of reach without squeezing the peaks flat (the limiter protects them)",
        "remix": "the music is already as low as it goes",
        "relayout": "the layout has no automatic repair: it is computed in studio/src/layout.ts",
        "rerender": "the same render gives the same picture: the layout has no automatic repair (studio/src/layout.ts)"}


@dataclass
class Action:
    kind: str
    scene: int = None
    codes: list = field(default_factory=list)
    data: dict = field(default_factory=dict)

    def key(self):
        return (self.kind, self.scene)

    def as_dict(self, ok=None):
        d = {"kind": self.kind, "scene": self.scene, "codes": list(self.codes)}
        if ok is not None:
            d["ok"] = bool(ok)
        return d


@dataclass
class Plan:
    actions: list = field(default_factory=list)
    manual: list = field(default_factory=list)            # [{code, scene, why}]: findings no automatic repair is left for

    @property
    def needs_render(self):
        return any(a.kind in TIMING or a.kind in PICTURE for a in self.actions)

    @property
    def audio_only(self):
        return bool(self.actions) and all(a.kind in AUDIO_ONLY for a in self.actions)


def blocking(findings):
    return [f for f in findings if f.sev in ("ERROR", "WARN")]


def score(findings):
    """(errors, warnings): smaller is better, compared as a tuple"""
    return (sum(1 for f in findings if f.sev == "ERROR"), sum(1 for f in findings if f.sev == "WARN"))


def scene_at(scenes, t):
    """1-based number of the scene a time belongs to (a little slack either side), else the scene that ended last before it; None without scenes"""
    best = None
    for i, s in enumerate(scenes or [], 1):
        try:
            a, b = float(s["start"]), float(s["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if a - 0.2 <= t <= b + 0.3:
            return i
        if b <= t:
            best = i
    return best


def _repair_for(f):
    """the first repair to try for a finding: the registry's, refined by what the detector measured"""
    kind = REGISTRY.get(f.code, {}).get("repair")
    where = (f.data or {}).get("where")
    if f.code == "VOICE_BREATH" and where in ("inside", "before"):
        return "retake_scene"                                    # a breath in the middle of a sentence cannot be trimmed away
    if f.code == "VOICE_BREATH" and where in ("tail", "after"):
        return "retrim_scene"
    if f.code == "EAR_BREATH":
        return "retrim_scene"
    if f.code == "VOICE_SUB" and kind == "respell" and not (f.data or {}).get("words"):
        return "retake_scene"
    return kind


def plan(findings, scenes=None, tried=None):
    """the repairs for the ERRORs and WARNs of one check. `tried` maps (code, scene) to the list of repairs already tried for it. Returns a Plan:
    actions sorted by ORDER (a new take of a scene replaces the smaller repairs of that scene), and manual: what nobody can repair automatically."""
    tried = tried if tried is not None else {}
    wanted = {}                                                  # (kind, scene) -> Action
    manual = []
    for f in blocking(findings):
        first = _repair_for(f)
        if first is None:
            continue
        scene = f.scene
        if first in PER_SCENE and scene is None and f.t is not None:
            scene = scene_at(scenes, float(f.t))
        if first in PER_SCENE and scene is None:
            manual.append({"code": f.code, "scene": None, "why": "no scene to repair: the fault is not tied to one sentence"})
            continue
        ladder = LADDER.get(first, [first])
        done = tried.get((f.code, scene if first in PER_SCENE else None), [])
        step = len(done)
        if step >= len(ladder):
            manual.append({"code": f.code, "scene": scene, "why": "automatic repairs are used up (" + ", ".join(done) + "): " + HINT.get(done[-1] if done else first, HINT[None])})
            continue
        kind = ladder[step]
        a = wanted.setdefault((kind, scene if kind in PER_SCENE else None), Action(kind, scene if kind in PER_SCENE else None))
        if f.code not in a.codes:
            a.codes.append(f.code)
        a.data.setdefault("findings", []).append({"code": f.code, "t": f.t, **{k: v for k, v in (f.data or {}).items() if isinstance(v, (int, float, str, bool, list))}})
    # a new take of a scene redoes every smaller repair of that scene
    for (kind, scene) in list(wanted):
        if kind in ("slow_scene", "retrim_scene", "level_scenes") and ("retake_scene", scene) in wanted:
            wanted[("retake_scene", scene)].codes += [c for c in wanted[(kind, scene)].codes if c not in wanted[("retake_scene", scene)].codes]
            del wanted[(kind, scene)]
        elif kind == "respell" and ("retake_scene", scene) in wanted:
            wanted[(kind, scene)].codes += [c for c in wanted[("retake_scene", scene)].codes if c not in wanted[(kind, scene)].codes]
            del wanted[("retake_scene", scene)]
    if ("relayout", None) in wanted and ("rerender", None) in wanted:
        del wanted[("rerender", None)]
    acts = sorted(wanted.values(), key=lambda a: (ORDER.index(a.kind), a.scene or 0))
    return Plan(acts, manual)


@dataclass
class LoopResult:
    findings: list
    history: list
    rounds: int
    stopped: str
    manual: list = field(default_factory=list)


def run_loop(check, apply, rebuild=None, scenes_of=None, max_rounds=3, snapshot=None, restore=None, log=None):
    """check -> plan -> apply every action -> rebuild once -> check again, up to `max_rounds` times. Stops when nothing blocks, when only manual findings are
    left, or when the rounds are used up. A round whose result is WORSE than the one before is undone with restore(snap) when snapshot()/restore() are given
    (the findings of the better state are returned). `scenes_of()` gives the current sidecar scenes (to find the scene of a finding that only has a time)."""
    log = log or (lambda *_a: None)
    findings = check()
    history, tried, manual = [], {}, []
    stopped = "clean" if not blocking(findings) else "rounds"
    rounds = 0
    for rnd in range(1, max_rounds + 1):
        if not blocking(findings):
            stopped = "clean"
            break
        p = plan(findings, scenes_of() if scenes_of else None, tried)
        manual = p.manual
        if not p.actions:
            stopped = "manual" if manual else "nothing to repair"
            break
        before = score(findings)
        snap = snapshot() if snapshot else None
        done = []
        for a in p.actions:
            try:
                ok = bool(apply(a))
            except Exception as e:                                # a repair that crashes is a repair that did not work, not a crashed loop
                log(f"repair {a.kind} scene {a.scene} failed: {e}")
                ok = False
            done.append(a.as_dict(ok))
            for code in a.codes:
                tried.setdefault((code, a.scene if a.kind in PER_SCENE else None), []).append(a.kind)
        if not any(d["ok"] for d in done):
            history.append({"round": rnd, "before": before, "actions": done, "after": before, "restored": False})
            stopped = "no repair worked"
            rounds = rnd
            break
        if rebuild:
            try:
                rebuild(p)
            except Exception as e:
                log(f"rebuild failed: {e}")
                if restore and snap is not None:
                    restore(snap)
                history.append({"round": rnd, "before": before, "actions": done, "after": before, "restored": True, "error": str(e)})
                stopped = "rebuild failed"
                rounds = rnd
                break
        new = check()
        after = score(new)
        restored = False
        if after > before and restore and snap is not None:
            restore(snap)
            restored = True
            log(f"round {rnd} made it worse ({before} -> {after}): undone")
        else:
            findings = new
        history.append({"round": rnd, "before": before, "actions": done, "after": after, "restored": restored})
        rounds = rnd
        if restored:
            stopped = "worse, undone"
            break
    else:
        stopped = "rounds"
    if not blocking(findings):
        stopped = "clean"
        manual = []
    else:                                                         # what is still open: nothing automatic left (manual) or only no rounds left
        rest = plan(findings, scenes_of() if scenes_of else None, tried)
        manual = rest.manual + [{"code": c, "scene": a.scene, "why": f"{a.kind} was not tried: the {max_rounds} rounds are used up"} for a in rest.actions for c in a.codes]
    return LoopResult(findings, history, rounds, stopped, manual)
