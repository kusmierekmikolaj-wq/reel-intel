"""quality/repair.py and quality/verdict.py: which repair for which fault, the ladder when a repair does not work, the loop with fakes (no GPU, no render), the verdict,
the exit codes and the report in Polish."""
import pytest

from reel_intel.quality import audio, engine, mix, picture, platform, repair, speech, sync, verdict  # noqa: F401  (importing registers every code)
from reel_intel.quality.base import REGISTRY, Finding, finding
from reel_intel.quality.repair import Action, plan, run_loop, score

SCENES = [{"start": 0.3, "end": 2.0}, {"start": 2.3, "end": 4.1}, {"start": 4.4, "end": 6.0}]


def F(code, scene=None, t=None, sev=None, **data):
    return finding(code, f"{code} test", sev=sev, scene=scene, t=t, **data)


def kinds(p):
    return [(a.kind, a.scene) for a in p.actions]


# ---------------------------------------------------------------- the register
def test_every_registered_code_has_a_polish_label_and_every_repair_is_known():
    assert [c for c in REGISTRY if c not in verdict.LABELS_PL] == []
    assert [c for c in verdict.LABELS_PL if c not in REGISTRY] == []
    assert {v["repair"] for v in REGISTRY.values()} - {None} <= set(repair.ORDER)
    assert set(repair.ORDER) <= set(verdict.ACTIONS_PL)


def test_the_action_classes_cover_every_action_once():
    assert repair.TIMING | repair.AUDIO_ONLY | repair.PICTURE == set(repair.ORDER)
    assert not (repair.TIMING & repair.AUDIO_ONLY) and not (repair.TIMING & repair.PICTURE)


# ---------------------------------------------------------------- plan
def test_nothing_to_do_for_a_clean_reel_or_for_information():
    assert plan([]).actions == []
    assert plan([F("PACE"), F("VIDEO_FACTS"), F("EARS_OFF"), F("VOICE_TAIL", scene=1)]).actions == []


def test_each_fault_gets_the_repair_of_the_register():
    p = plan([F("VOICE_CUT", scene=2), F("VOICE_FAST", scene=3), F("VOICE_SUB", scene=1, words=["skill"]), F("MIX_PUMPING"), F("LOUDNESS"), F("SAFE_ZONE"), F("PICTURE_BLACK")])
    assert kinds(p) == [("respell", 1), ("retake_scene", 2), ("slow_scene", 3), ("remix", None), ("master", None), ("relayout", None)]      # relayout renders again anyway
    assert p.manual == []
    assert kinds(plan([F("PICTURE_BLACK")])) == [("rerender", None)]


def test_a_breath_inside_a_sentence_is_retaken_but_a_breathy_tail_is_trimmed():
    assert kinds(plan([F("VOICE_BREATH", scene=1, where="inside")])) == [("retake_scene", 1)]
    assert kinds(plan([F("VOICE_BREATH", scene=1, where="tail")])) == [("retrim_scene", 1)]
    assert kinds(plan([F("VOICE_BREATH", scene=1, where="after")])) == [("retrim_scene", 1)]


def test_an_ear_finding_between_scenes_is_given_to_the_scene_that_just_ended():
    p = plan([F("EAR_BREATH", t=4.2, where="between")], SCENES)
    assert kinds(p) == [("retrim_scene", 2)]
    p = plan([F("EAR_BREATH", t=1.0, scene=1, where="scene")], SCENES)
    assert kinds(p) == [("retrim_scene", 1)]


def test_a_per_scene_fault_without_a_scene_and_a_time_goes_to_a_person():
    p = plan([F("VOICE_CUT")])
    assert p.actions == [] and p.manual and p.manual[0]["code"] == "VOICE_CUT"


def test_a_substitution_with_no_words_is_retaken_not_respelled():
    assert kinds(plan([F("VOICE_SUB", scene=2)])) == [("retake_scene", 2)]


def test_errors_and_warnings_both_count_but_info_does_not():
    assert kinds(plan([F("VOICE_CUT", scene=1, sev="INFO")])) == []
    assert kinds(plan([F("VOICE_PAUSE_INNER", scene=1, sev="WARN")])) == [("retake_scene", 1)]


def test_several_faults_of_one_scene_make_one_action_that_lists_them():
    p = plan([F("VOICE_DEL", scene=2), F("VOICE_INS", scene=2), F("VOICE_PAUSE_INNER", scene=2)])
    assert kinds(p) == [("retake_scene", 2)] and sorted(p.actions[0].codes) == ["VOICE_DEL", "VOICE_INS", "VOICE_PAUSE_INNER"]


def test_a_new_take_makes_the_smaller_repairs_of_that_scene_unnecessary():
    p = plan([F("VOICE_CUT", scene=2), F("VOICE_DRONE", scene=2), F("VOICE_FAST", scene=2), F("VOICE_FAST", scene=3), F("VOICE_DRONE", scene=1)])
    assert kinds(p) == [("retrim_scene", 1), ("retake_scene", 2), ("slow_scene", 3)] or kinds(p) == [("retake_scene", 2), ("slow_scene", 3), ("retrim_scene", 1)]
    assert sorted(p.actions[[a.kind for a in p.actions].index("retake_scene")].codes) == ["VOICE_CUT", "VOICE_DRONE", "VOICE_FAST"]


def test_a_respell_swallows_a_retake_of_the_same_scene():
    p = plan([F("VOICE_SUB", scene=1, words=["skill"]), F("VOICE_PAUSE_INNER", scene=1)])
    assert kinds(p) == [("respell", 1)] and "VOICE_PAUSE_INNER" in p.actions[0].codes


def test_relayout_makes_a_plain_rerender_unnecessary():
    assert kinds(plan([F("SAFE_ZONE"), F("PICTURE_FREEZE")])) == [("relayout", None)] or kinds(plan([F("SAFE_ZONE"), F("PICTURE_FREEZE")])) == [("relayout", None), ("rerender", None)]
    p = plan([F("SAFE_ZONE"), F("PICTURE_BLACK")])
    assert ("rerender", None) not in kinds(p) and ("relayout", None) in kinds(p)


def test_actions_come_in_the_documented_order():
    p = plan([F("PICTURE_BLACK"), F("MIX_PUMPING"), F("VOICE_FAST", scene=3), F("VOICE_CUT", scene=1), F("LOUDNESS")])
    assert [a.kind for a in p.actions] == ["retake_scene", "slow_scene", "remix", "master", "rerender"]


def test_needs_render_and_audio_only():
    assert plan([F("LOUDNESS"), F("MIX_PUMPING")]).audio_only and not plan([F("LOUDNESS")]).needs_render
    assert plan([F("VOICE_CUT", scene=1)]).needs_render and not plan([F("VOICE_CUT", scene=1)]).audio_only
    assert not plan([]).audio_only and not plan([]).needs_render


def test_the_ladder_escalates_after_a_failed_try_and_ends_in_manual():
    f = [F("VOICE_DRONE", scene=2)]
    tried = {}
    p = plan(f, SCENES, tried)
    assert kinds(p) == [("retrim_scene", 2)]
    tried[("VOICE_DRONE", 2)] = ["retrim_scene"]
    assert kinds(plan(f, SCENES, tried)) == [("retake_scene", 2)]
    tried[("VOICE_DRONE", 2)] = ["retrim_scene", "retake_scene"]
    p = plan(f, SCENES, tried)
    assert p.actions == [] and p.manual[0]["code"] == "VOICE_DRONE" and "used up" in p.manual[0]["why"]


def test_two_retakes_are_the_most_for_one_fault():
    f = [F("VOICE_CUT", scene=1)]
    assert kinds(plan(f, None, {("VOICE_CUT", 1): ["retake_scene"]})) == [("retake_scene", 1)]
    assert plan(f, None, {("VOICE_CUT", 1): ["retake_scene", "retake_scene"]}).actions == []


def test_a_global_fault_escalates_remix_to_master():
    f = [F("MIX_PUMPING")]
    assert kinds(plan(f, None, {("MIX_PUMPING", None): ["remix"]})) == [("master", None)]


def test_score_is_errors_then_warnings():
    assert score([F("VOICE_CUT", sev="ERROR"), F("VOICE_FAST", sev="WARN"), F("PACE")]) == (1, 1)
    assert (0, 9) < (1, 0)


# ---------------------------------------------------------------- the loop with a fake reel
class World:
    """a reel that is a set of faults; each repair removes the faults it is able to remove"""

    def __init__(self, faults, fixes):
        self.faults = list(faults)                     # [(code, scene, sev)]
        self.fixes = fixes                             # (kind, scene) -> codes it removes (None: removes nothing)
        self.applied, self.rebuilt, self.snapshots = [], [], []

    def check(self):
        return [Finding(sev, code, f"{code} in {scene}", scene, None, {}) for code, scene, sev in self.faults]

    def apply(self, a):
        self.applied.append((a.kind, a.scene))
        gone = self.fixes.get((a.kind, a.scene))
        if gone is None:
            return True
        self.faults = [f for f in self.faults if not (f[0] in gone and f[1] == a.scene)]
        return True

    def rebuild(self, p):
        self.rebuilt.append((p.needs_render, p.audio_only))

    def run(self, **kw):
        return run_loop(self.check, self.apply, self.rebuild, scenes_of=lambda: SCENES, **kw)


def test_loop_on_a_clean_reel_does_nothing():
    w = World([], {})
    r = w.run()
    assert r.stopped == "clean" and r.rounds == 0 and w.applied == [] and w.rebuilt == [] and r.history == []


def test_loop_repairs_and_checks_again():
    w = World([("VOICE_CUT", 2, "ERROR"), ("LOUDNESS", None, "WARN")], {("retake_scene", 2): {"VOICE_CUT"}, ("master", None): {"LOUDNESS"}})
    r = w.run()
    assert r.stopped == "clean" and r.rounds == 1 and w.applied == [("retake_scene", 2), ("master", None)]
    assert len(w.rebuilt) == 1                                           # one render for the whole round
    assert r.history[0]["before"] == (1, 1) and r.history[0]["after"] == (0, 0) and all(a["ok"] for a in r.history[0]["actions"])
    assert r.findings == [] and r.manual == []


def test_loop_escalates_over_the_rounds_and_stops_at_manual():
    w = World([("VOICE_DRONE", 1, "WARN")], {("retrim_scene", 1): set(), ("retake_scene", 1): set()})        # nothing works
    r = w.run()
    assert [a for a in w.applied] == [("retrim_scene", 1), ("retake_scene", 1)]
    assert r.stopped == "manual" and r.rounds == 2 and r.manual and r.manual[0]["code"] == "VOICE_DRONE"


def test_loop_stops_after_three_rounds():
    w = World([("VOICE_CUT", 1, "ERROR"), ("VOICE_FAST", 2, "WARN"), ("MIX_PUMPING", None, "WARN")], {})
    r = w.run(max_rounds=3)
    assert r.rounds <= 3 and r.stopped in ("manual", "rounds")
    r1 = World([("MIX_PUMPING", None, "WARN")], {}).run(max_rounds=1)
    assert r1.rounds == 1 and r1.stopped == "rounds" and r1.manual and "used up" in r1.manual[0]["why"]


def test_a_repair_that_crashes_is_not_a_crashed_loop():
    w = World([("VOICE_CUT", 1, "ERROR"), ("LOUDNESS", None, "WARN")], {("master", None): {"LOUDNESS"}})
    real = w.apply

    def apply(a):
        if a.kind == "retake_scene":
            raise RuntimeError("the voice engine died")
        return real(a)
    log = []
    r = run_loop(w.check, apply, w.rebuild, scenes_of=lambda: SCENES, log=log.append)
    assert any("voice engine died" in l for l in log)
    first = r.history[0]["actions"]
    assert {a["kind"]: a["ok"] for a in first} == {"retake_scene": False, "master": True}
    assert score(r.findings) == (1, 0)


def test_when_no_repair_works_the_loop_does_not_spin():
    w = World([("VOICE_CUT", 1, "ERROR")], {})
    calls = []

    def apply(a):
        calls.append(a.kind)
        return False
    r = run_loop(w.check, apply, w.rebuild, max_rounds=3)
    assert calls == ["retake_scene"] and r.stopped == "no repair worked" and w.rebuilt == []


def test_a_round_that_makes_things_worse_is_undone_when_the_caller_can():
    state = {"faults": [("VOICE_FAST", 1, "WARN")]}
    saved = []

    def check():
        return [Finding(s, c, c, n, None, {}) for c, n, s in state["faults"]]

    def apply(a):
        state["faults"] = [("VOICE_CUT", 1, "ERROR"), ("VOICE_BREATH", 1, "WARN")]              # a bad take
        return True

    def snapshot():
        return list(state["faults"])

    def restore(snap):
        saved.append(snap)
        state["faults"] = list(snap)
    r = run_loop(check, apply, None, snapshot=snapshot, restore=restore)
    assert saved == [[("VOICE_FAST", 1, "WARN")]] and r.stopped == "worse, undone" and r.history[0]["restored"] is True
    assert score(r.findings) == (0, 1)                                    # the better state is what is reported
    r2 = run_loop(check, apply, None)                                     # without snapshot/restore the worse state stays and is reported
    assert score(r2.findings)[0] == 1


def test_a_failed_rebuild_restores_and_stops():
    w = World([("VOICE_CUT", 1, "ERROR")], {("retake_scene", 1): {"VOICE_CUT"}})
    restored = []

    def rebuild(p):
        raise RuntimeError("remotion failed")
    r = run_loop(w.check, w.apply, rebuild, snapshot=lambda: "snap", restore=restored.append)
    assert restored == ["snap"] and r.stopped == "rebuild failed" and r.history[0]["restored"] is True


def test_the_loop_gives_a_time_only_finding_its_scene():
    state = {"f": [Finding("WARN", "EAR_BREATH", "x", None, 4.2, {"where": "between"})]}
    seen = []

    def apply(a):
        seen.append((a.kind, a.scene))
        state["f"] = []
        return True
    r = run_loop(lambda: state["f"], apply, None, scenes_of=lambda: SCENES)
    assert seen == [("retrim_scene", 2)] and r.stopped == "clean"


# ---------------------------------------------------------------- the verdict
def test_decide_and_exit_codes():
    assert verdict.decide([]) == "gotowe" and verdict.exit_code("gotowe") == 0
    assert verdict.decide([F("PACE"), F("EARS_OFF")]) == "gotowe"
    assert verdict.decide([F("VOICE_FAST", scene=1)]) == "z uwagami" and verdict.exit_code("z uwagami") == 10
    assert verdict.decide([F("VOICE_FAST", scene=1), F("VOICE_CUT", scene=1, sev="ERROR")]) == "niegotowe" and verdict.exit_code("niegotowe") == 1
    assert verdict.exit_code("error") == 2 and verdict.exit_code("whatever") == 2


def test_many_warnings_are_still_only_notes():
    assert verdict.decide([F("VOICE_FAST", scene=i) for i in range(1, 30)]) == "z uwagami"


def test_summarize_is_json_able_and_complete():
    import json
    fs = [F("VOICE_CUT", scene=2, t=3.1, sev="ERROR"), F("LOUDNESS"), F("OCR_OFF"), F("PACE")]
    hist = [{"round": 1, "before": (1, 1), "actions": [{"kind": "retake_scene", "scene": 2, "ok": True}], "after": (0, 1)}]
    s = verdict.summarize(fs, hist, checked=["audio", "mix"])
    json.dumps(s)
    assert s["verdict"] == "niegotowe" and s["exit"] == 1 and s["errors"] == 1 and s["warnings"] == 1 and s["info"] == 2
    assert s["codes"] == {"LOUDNESS": 1, "VOICE_CUT": 1} and s["scenes"] == [2] and s["not_checked"] == ["OCR_OFF"]
    assert s["findings"][0]["code"] == "VOICE_CUT" and s["rounds"][0]["actions"][0]["kind"] == "retake_scene" and s["checked"] == ["audio", "mix"]


def test_report_in_polish_has_the_verdict_the_findings_and_the_limits():
    fs = [F("VOICE_CUT", scene=2, t=3.1, sev="ERROR"), F("LOUDNESS"), F("OCR_OFF")]
    hist = [{"round": 1, "before": (1, 1), "actions": [{"kind": "retake_scene", "scene": 2, "ok": False}], "after": (1, 1)}]
    md = verdict.report_pl(fs, hist, title="Kontrola rolki A")
    assert "# Kontrola rolki A" in md and "Werdykt: niegotowe" in md
    assert "ucięte ostatnie słowo" in md and "scena 2, 3.1 s" in md and "VOICE_CUT" in md
    assert "nagrano zdanie od nowa" in md and "NIE UDAŁO SIĘ" in md
    assert "Czego nie sprawdzono" in md and "Czego ten system nie ocenia" in md and "odsłuch" in md
    ready = verdict.report_pl([F("PACE")])
    assert "Werdykt: gotowe" in ready and "Nic poważniejszego niż informacje" in ready and "## Błędy" not in ready


def test_console_text_hides_noise_and_shows_the_verdict():
    txt = verdict.format_console([F("VOICE_CUT", scene=2, sev="ERROR"), F("VIDEO_FACTS"), F("OCR_OFF")])
    assert "VOICE_CUT" in txt and "VIDEO_FACTS" not in txt and "OCR_OFF" in txt and txt.strip().endswith("werdykt: NIEGOTOWE (1 błędów, 0 uwag)")


def test_label_falls_back_to_the_registry_and_then_the_code():
    assert verdict.label("VOICE_CUT") == "ucięte ostatnie słowo"
    assert verdict.label("NO_SUCH_CODE") == "NO_SUCH_CODE"


def test_the_history_names_the_codes_before_and_after_each_round():
    w = World([("VOICE_CUT", 2, "ERROR"), ("LOUDNESS", None, "WARN")], {("retake_scene", 2): {"VOICE_CUT"}})
    r = w.run(max_rounds=1)
    h = r.history[0]
    assert h["before_codes"] == ["LOUDNESS", "VOICE_CUT"] and h["after_codes"] == ["LOUDNESS"] and h["restored"] is False
    s = verdict.summarize(r.findings, r.history)
    assert s["rounds"][0]["before_codes"] == ["LOUDNESS", "VOICE_CUT"] and s["rounds"][0]["after_codes"] == ["LOUDNESS"]
    assert "[LOUDNESS, VOICE_CUT]" in verdict.report_pl(r.findings, r.history) and "['LOUDNESS']" in verdict.format_console(r.findings, r.history)
