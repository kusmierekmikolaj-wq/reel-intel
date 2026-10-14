"""qa.py - strict verifier of scan results. Every finding has a code, severity and a concrete fix.

  reel.py qa                 # check all reels -> <data>/qa_report.md (+ .json)
  reel.py qa <id> [<id>...]  # selected reels
  reel.py qa --selftest      # inject known faults into a copy of a good reel and prove each one is caught
Severity: ERROR = data missing/wrong, WARN = suspicious, INFO = context. Exit code 1 when any ERROR exists.
"""
import collections
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .common import CFG, DATA, GLOSS, WORK, duration, jload, reel_id, reel_prompt, run, views_table

REQUIRED = ["meta.json", "audio.wav", "transcript.json", "features.json", "content_full.md"]


def add(out, reel, sev, code, msg, fix=""):
    out.append({"reel": reel, "severity": sev, "code": code, "msg": msg, "fix": fix})


def check_environment():
    out = []
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            add(out, "ENV", "ERROR", f"ENV_NO_{tool.upper()}", f"{tool} not on PATH", "install ffmpeg (winget install Gyan.FFmpeg / brew install ffmpeg)")
    try:
        import av
        if int(av.__version__.split(".")[0]) >= 17:
            add(out, "ENV", "ERROR", "ENV_AV_VERSION", f"PyAV {av.__version__} breaks faster-whisper", 'pip install "av>=14,<17"')
    except Exception as e:
        add(out, "ENV", "ERROR", "ENV_AV", f"PyAV import failed: {e}", "reel.py setup")
    if CFG["language"]:
        add(out, "ENV", "WARN", "ENV_FORCED_LANGUAGE", f"language forced to '{CFG['language']}': other languages will come out as garbage", "set language: null")
    return out


def repeat_runs(segs, norm, run_min=3, window_s=45.0, window_n=4):
    """A Whisper loop is the SAME segment again and again: either one after another (a run), or densely (4+ times within 45 s).
    The same short phrase turning up now and then over a 40-minute talk is just speech, and is not reported."""
    out, i = [], 0
    while i < len(segs):
        j = i
        while j + 1 < len(segs) and norm[j + 1] == norm[i]:
            j += 1
        n = j - i + 1
        if n >= run_min and len(norm[i]) > 3:
            out.append((norm[i], n, [w["p"] for s in segs[i:j + 1] for w in s["words"]]))
        i = j + 1
    reported = {o[0] for o in out}
    where = {}
    for k, txt in enumerate(norm):
        if len(txt) > 3 and txt not in reported:
            where.setdefault(txt, []).append(k)
    for txt, ks in where.items():
        for x in range(len(ks) - window_n + 1):
            if segs[ks[x + window_n - 1]]["start"] - segs[ks[x]]["start"] <= window_s:
                out.append((txt, window_n, [w["p"] for k in ks[x:x + window_n] for w in segs[k]["words"]]))
                break
    return out


def check_transcript(rid, d, adur, out):
    t = jload(d / "transcript.json")
    segs = t["segments"]
    words = [w for s in segs for w in s["words"]]
    if t.get("prompt_hash") != reel_prompt(jload(d / "meta.json"), t["language"])[1]:
        add(out, rid, "WARN", "T_PROMPT_STALE", "transcript made without the current glossary + caption-names prompt (names/jargon may be misheard)", "delete transcript.json + features.json + content_full.md, rescan")
    raw = " ".join(s["text"] for s in segs)
    for fx in GLOSS.get("fixes", []):
        if fx.get("lang") and fx["lang"] != t["language"]:          # a Polish fix never fires on an English reel
            continue
        m = re.search(fx["wrong"], raw, re.I)
        if m:
            add(out, rid, "WARN", "T_GLOSSARY", f"known mishearing '{m.group(0)}' -> '{fx['right']}'", "corrected in content_full.md; keep glossary.yaml current")
    if not segs:
        sheet = (d / "contact_sheet_1.jpg").exists()
        add(out, rid, "INFO" if sheet else "WARN", "T_EMPTY", "no speech detected", "visual/music reel: content is read from the contact sheet" if sheet else "visual/music reel: needs a contact sheet")
        return t
    if not CFG["language"] and t["language"] != "pl" and t["language_probability"] >= 0.8:
        add(out, rid, "INFO", "T_NOT_PL", f"language {t['language']} ({t['language_probability']})", "")
    norm = [re.sub(r"\W+", " ", s["text"].lower()).strip() for s in segs]
    for txt, n, conf in repeat_runs(segs, norm):
        spoken = len(txt.split()) < 3 and (n < 6 or (conf and sum(conf) / len(conf) >= 0.85))      # one or two words, said again and again: a speaker can do that
        if spoken:
            add(out, rid, "WARN", "T_REPEAT", f"'{txt[:50]}' appears {n}x in a row; probably spoken that way (short, confident): check the recording", "")
        else:
            add(out, rid, "ERROR", "T_REPEAT", f"segment repeated {n}x in a row: '{txt[:50]}' (Whisper loop)", "rescan with condition_on_previous_text=False")
    if re.search(r"[Ѐ-ӿ一-鿿؀-ۿऀ-ॿ]", raw):
        add(out, rid, "ERROR", "T_FOREIGN_SCRIPT", "non-Latin characters (hallucination or wrong language)", "rescan with the right language")
    if words:
        low = sum(1 for w in words if w["p"] < 0.4) / len(words)
        if low > 0.12:
            add(out, rid, "WARN", "T_LOW_CONF", f"{low:.0%} of words have confidence < 0.4", "noisy audio/music: verify manually")
        prev = -1
        for w in words:
            if w["e"] < w["s"] or w["s"] < prev - 0.05:
                add(out, rid, "ERROR", "T_TIMESTAMPS", f"non-monotonic word time near {w['s']}s", "rescan")
                break
            prev = w["s"]
        span = max(words[-1]["e"] - words[0]["s"], 1)                # speaking span, not clip length: one sentence in a long clip is fine
        wpm = len(words) / (span / 60)
        if (wpm < 40 or wpm > 280) and len(words) >= 10:
            add(out, rid, "WARN", "T_WPM", f"{wpm:.0f} words/min is implausible", "check language/music")
    from .common import word_holes
    for si, wi, a, b in word_holes(segs)[:3]:
        if t.get("holes_checked"):
            add(out, rid, "INFO", "T_HOLE_SILENT", f"no words between {a:.0f}s and {b:.0f}s inside one segment; re-checked, nothing speech-like found", "")
        else:
            add(out, rid, "WARN", "T_HOLE", f"no words between {a:.0f}s and {b:.0f}s inside one segment (speech may have been skipped)", "delete transcript.json and rescan (recovers the gap)")
    tail = adur - segs[-1]["end"]
    if tail > 6 and len(words) >= 10:
        if t.get("tail_checked"):
            add(out, rid, "INFO", "T_TAIL_SILENT", f"transcript ends {tail:.1f}s before audio end; re-checked without the voice filter, no further speech", "")
        else:
            add(out, rid, "WARN", "T_TAIL_GAP", f"transcript ends {tail:.1f}s before audio end", "possible VAD truncation: delete transcript.json and rescan (recovers the tail)")
    return t


def check_reel(rid, root, views):
    out = []
    d = root / rid
    for n in REQUIRED:
        if not (d / n).exists():
            add(out, rid, "ERROR", "MISSING_" + n.split(".")[0], f"{n} missing", "reel.py scan <url>")
    if out:
        return out
    meta = jload(d / "meta.json")
    if rid not in (meta.get("id"), reel_id(meta.get("webpage_url") or ""), f"{reel_id(meta.get('webpage_url') or '')}"):   # other platforms: folder = prefix + platform id
        add(out, rid, "ERROR", "M_ID_MISMATCH", f"meta id {meta.get('id')} != folder {rid}", "delete the folder and rescan")
    try:
        adur = duration(d / "audio.wav")
    except ValueError:
        add(out, rid, "ERROR", "A_UNREADABLE", "audio.wav unreadable", "delete audio.wav and rescan")
        return out
    tr = check_transcript(rid, d, adur, out)
    f = jload(d / "features.json")
    if (d / "visual.json").exists():                                  # second, independent reading of the speech: captions burned into the picture
        v = jload(d / "visual.json")
        if v.get("ocr_asr_overlap") is not None and v["ocr_asr_overlap"] < 0.35:
            add(out, rid, "INFO", "V_ASR_OCR_LOW", f"captions in the picture and the transcript share only {v['ocr_asr_overlap']:.0%} of their letters",
                "look at the video: the captions may be a translation or edited text, or the transcript may be wrong")
    for k in ("views", "likes", "comments", "language", "word_count", "duration_s"):
        if f.get(k) is None:
            add(out, rid, "WARN" if k in ("views", "likes") else "INFO" if k == "comments" else "ERROR", "X_NULL_" + k.upper(), f"features.{k} is null",
                "views hidden on the grid: add via reel-intel scan --grid" if k == "views" else "some platforms (e.g. YouTube) do not give comment counts" if k == "comments" else "rescan")
    if f.get("word_count") != sum(len(s["words"]) for s in tr["segments"]):
        add(out, rid, "ERROR", "X_STALE", "features.word_count != transcript words", "delete features.json + content_full.md, rescan")
    if f.get("views") and f.get("likes") and f["likes"] > f["views"]:
        add(out, rid, "ERROR", "X_LIKES_GT_VIEWS", f"likes {f['likes']} > views {f['views']}", "metrics wrong")
    if views.get(rid) is not None and f.get("views") is not None and f["views"] != views[rid]:
        add(out, rid, "ERROR", "X_VIEWS_MISMATCH", f"features views {f['views']} != views.csv {views[rid]}", "delete features.json + content_full.md, rescan")
    if f.get("word_count", 99) < 10 or not f.get("speech_reliable", True):
        # music/visual reel: Whisper noise warnings are expected here, the content is read from the sheet
        out[:] = [i for i in out if i["code"] not in ("T_LOW_CONF", "T_WPM", "T_TAIL_GAP", "T_NOT_PL", "T_FOREIGN_SCRIPT")]
        if (d / "contact_sheet_1.jpg").exists():
            add(out, rid, "INFO", "V_VISUAL_REEL", "no usable speech: content is visual, read contact_sheet_1.jpg", "")
        else:
            add(out, rid, "WARN", "V_NO_SHEET", "no usable speech and no contact sheet: content would be lost", "reel.py scan <url>")
    if f.get("comments") and f.get("likes") and f["comments"] > 2 * f["likes"]:
        add(out, rid, "INFO", "X_COMMENT_TRIGGER", f"comments {f['comments']} >> likes {f['likes']}: comment-keyword CTA pattern", "")
    if not (d / "content_short.md").exists():
        add(out, rid, "INFO", "MISSING_content_short", "content_short.md missing", "run `reel-intel shorten` (automatic extract) or let the Claude skill write it")
    return out


def cross_reel(root, ids, out):
    seen = collections.defaultdict(list)
    for rid in ids:
        p = root / rid / "transcript.json"
        if p.exists():
            txt = re.sub(r"\W", "", " ".join(s["text"] for s in jload(p)["segments"]).lower())
            if len(txt) > 20:
                seen[txt].append(rid)
    for rs in seen.values():
        if len(rs) > 1:
            add(out, ",".join(rs), "WARN", "C_DUPLICATE", "identical transcripts (duplicate/cross-post or same music)", "dedupe before statistics")


def write_report(issues, ids):
    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    issues = sorted(issues, key=lambda i: (order[i["severity"]], i["code"], i["reel"]))
    import json
    (WORK / "qa_report.json").write_text(json.dumps(issues, ensure_ascii=False, indent=1), encoding="utf-8")
    c = collections.Counter(i["severity"] for i in issues)
    L = ["# QA report", "", f"Reels: {len(ids)} | ERROR {c['ERROR']} | WARN {c['WARN']} | INFO {c['INFO']}", "", "## Fix first (grouped by cause)", "",
         "| sev | code | reels | fix |", "|---|---|---|---|"]
    by = collections.defaultdict(list)
    for i in issues:
        by[(i["severity"], i["code"])].append(i)
    for (sev, code), lst in sorted(by.items(), key=lambda kv: (order[kv[0][0]], -len(kv[1]))):
        L.append(f"| {sev} | {code} | {len(lst)} | {lst[0]['fix'] or '-'} |")
    L += ["", "## Details", ""] + [f"- **{i['severity']}** `{i['code']}` [{i['reel']}] {i['msg']}" + (f" -> _{i['fix']}_" if i["fix"] else "") for i in issues]
    (WORK / "qa_report.md").write_text("\n".join(L), encoding="utf-8")
    return c


def selftest():
    good = next((p for p in DATA.iterdir() if (p / "content_full.md").exists() and (p / "features.json").exists()
                 and jload(p / "features.json").get("word_count", 0) > 60), None)
    if not good:
        print("selftest needs one scanned reel with speech")
        return 2
    rid, fails = good.name, 0
    tmp = Path(tempfile.mkdtemp()) / "data"
    import json

    def edit(p, fn):
        j = jload(p)
        fn(j)
        p.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")

    def case(name, expect, mutate, views=None, absent=False):
        nonlocal fails
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(good, tmp / rid)
        mutate(tmp / rid)
        codes = {i["code"] for i in check_reel(rid, tmp, views or {})}
        ok = (expect not in codes) if absent else (expect in codes)
        fails += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {name:34s} expects {'NO ' if absent else ''}{expect}")

    tr, ft = "transcript.json", "features.json"
    case("delete transcript", "MISSING_transcript", lambda d: (d / tr).unlink())
    case("missing content file", "MISSING_content_full", lambda d: (d / "content_full.md").unlink())
    def truncate(j):                                                # keep >= 12 words (fewer would count as a visual reel), drop the rest
        keep, n = 0, 0
        for s in j["segments"]:
            keep += 1
            n += len(s["words"])
            if n >= 12:
                break
        j["segments"] = j["segments"][:keep]
        j.pop("tail_checked", None)

    case("truncated transcript", "T_TAIL_GAP", lambda d: edit(d / tr, truncate))

    def hole(j):                                                     # push the second half of the first long segment 20 s later: a skipped stretch
        j.pop("holes_checked", None)
        seg = max(j["segments"], key=lambda x: len(x["words"]))
        for w in seg["words"][len(seg["words"]) // 2:]:
            w["s"] += 20.0
            w["e"] += 20.0

    case("speech skipped inside a segment", "T_HOLE", lambda d: edit(d / tr, hole))
    case("whisper loop", "T_REPEAT", lambda d: edit(d / tr, lambda j: j["segments"].extend([dict(j["segments"][0])] * 4)))
    case("cyrillic text", "T_FOREIGN_SCRIPT", lambda d: edit(d / tr, lambda j: j["segments"][0].update(text="привет мир")))
    case("low confidence words", "T_LOW_CONF", lambda d: edit(d / tr, lambda j: [w.update(p=0.1) for s in j["segments"] for w in s["words"]]))
    case("known mishearing", "T_GLOSSARY", lambda d: edit(d / tr, lambda j: (j.update(language="pl"), j["segments"][0].update(text="miałeś niezły huk"))))
    if any(fx.get("lang") for fx in GLOSS.get("fixes", [])):       # only meaningful when the glossary has language-tagged fixes
        case("polish fix must not hit an english reel", "T_GLOSSARY",
             lambda d: edit(d / tr, lambda j: (j.update(language="en"), j["segments"][0].update(text="he said huk"))), absent=True)
    else:
        print("SKIP  language-tagged fix check (glossary has no `lang:` fixes)")
    case("transcript without glossary", "T_PROMPT_STALE", lambda d: edit(d / tr, lambda j: j.pop("prompt_hash")))
    case("likes > views", "X_LIKES_GT_VIEWS", lambda d: edit(d / ft, lambda j: j.update(views=10, likes=500)))
    case("stale features", "X_STALE", lambda d: edit(d / ft, lambda j: j.update(word_count=1)))
    case("views mismatch", "X_VIEWS_MISMATCH", lambda d: edit(d / ft, lambda j: j.update(views=123)), views={rid: 456})
    case("broken audio", "A_UNREADABLE", lambda d: (d / "audio.wav").write_bytes(b"garbage"))
    case("visual reel without sheet", "V_NO_SHEET",
         lambda d: (edit(d / ft, lambda j: j.update(word_count=3)), (d / "contact_sheet_1.jpg").unlink(missing_ok=True)))
    shutil.rmtree(tmp.parent, ignore_errors=True)
    print(f"\nselftest: {'ALL CAUGHT' if not fails else str(fails) + ' fault(s) NOT detected'}")
    return 1 if fails else 0


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        sys.exit(selftest())
    ids = args or sorted(p.name for p in DATA.iterdir() if p.is_dir() and (p / "meta.json").exists())
    views = views_table()
    issues = check_environment()
    for rid in ids:
        issues += check_reel(rid, DATA, views)
    cross_reel(DATA, ids, issues)
    c = write_report(issues, ids)
    print(f"checked {len(ids)} reels: ERROR {c['ERROR']}  WARN {c['WARN']}  INFO {c['INFO']}  -> {WORK / 'qa_report.md'}")
    sys.exit(1 if c["ERROR"] else 0)


if __name__ == "__main__":
    main()
