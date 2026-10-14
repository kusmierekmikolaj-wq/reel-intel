"""Unit tests for the pure parts (no network, no models). Run: pytest"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("REEL_DATA", tempfile.mkdtemp(prefix="reel_test_"))   # before the package reads it

import pytest  # noqa: E402

from reel_intel import common, page, report, scan  # noqa: E402


# ---- ids -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("url,expected", [
    ("https://www.instagram.com/someone/reel/DB9W8M0tqD6/", "DB9W8M0tqD6"),
    ("https://www.instagram.com/reel/DB9W8M0tqD6/?igsh=abc", "DB9W8M0tqD6"),
    ("https://www.instagram.com/p/C_QG-vrNVB9/", "C_QG-vrNVB9"),
    ("https://www.tiktok.com/@user/video/7234567890123456789", "tt_7234567890123456789"),
    ("https://www.youtube.com/shorts/abcDEF12345", "yt_abcDEF12345"),
    ("https://youtu.be/abcDEF12345", "yt_abcDEF12345"),
    ("https://www.youtube.com/watch?v=abcDEF12345&t=3", "yt_abcDEF12345"),
    ("https://x.com/user/status/1234567890", "x_1234567890"),
    ("https://www.facebook.com/reel/123456789", "fb_123456789"),
])
def test_reel_id(url, expected):
    assert common.reel_id(url) == expected


def test_reel_id_unknown_site_is_stable_hash():
    a = common.reel_id("https://example.org/some/video.mp4")
    assert a.startswith("v_") and a == common.reel_id("https://example.org/some/video.mp4")


# ---- numbers ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [("1,2 mln", 1_200_000), ("40,2 tys.", 40_200), ("9249", 9249), ("215 tys.", 215_000), ("12.5k", 12_500), ("22 tys.", 22_000)])
def test_parse_views(raw, expected):
    assert common.parse_views(raw) == expected


def test_read_grid(tmp_path):
    g = tmp_path / "grid.tsv"
    g.write_text("#profile\tsomeone\nAAA111\t40,2 tys.\nBBB222\t9249\nCCC333\twhat\n", encoding="utf-8")
    handle, rows, links = scan.read_grid(g)
    assert handle == "someone" and rows == [("AAA111", 40_200), ("BBB222", 9249), ("CCC333", None)] and links == {}


def test_read_grid_with_full_links(tmp_path):
    g = tmp_path / "grid.tsv"
    g.write_text("#profile\tx\nhttps://www.tiktok.com/@x/video/123\t1,2 mln\nhttps://www.youtube.com/shorts/abcDEF12345\t5 tys.\n", encoding="utf-8")
    handle, rows, links = scan.read_grid(g)
    assert rows == [("tt_123", 1_200_000), ("yt_abcDEF12345", 5000)] and links["tt_123"].endswith("/video/123")


# ---- prompts and glossary --------------------------------------------------------------------------------------------
def test_caption_names_keeps_names_not_sentence_starts():
    names = common.caption_names('Comment "MATCH" and I will send the course. Ask @jane about Meta Ads and ChatGPT. Today we start.')
    assert "MATCH" in names and "jane" in names and "ChatGPT" in names
    assert "Today" not in names and "Comment" not in names


def test_reel_prompt_is_deterministic_and_language_specific():
    meta = {"description": "Join the Kimi challenge"}
    p1, h1 = common.reel_prompt(meta, "en")
    p2, h2 = common.reel_prompt(meta, "en")
    assert (p1, h1) == (p2, h2) and "Kimi" in p1
    assert common.reel_prompt(meta, "pl")[1] != h1 or common.glossary_prompt("pl") == common.glossary_prompt("en")


def test_polish_fix_applies_only_to_polish():
    txt = "miałeś niezły huk"
    assert common.apply_fixes(txt, "pl")[0].endswith("hook")
    assert common.apply_fixes(txt, "en")[0] == txt


# ---- statistics ------------------------------------------------------------------------------------------------------
def test_spearman_and_permutation_p():
    x = list(range(20))
    assert report.spearman(x, x) == pytest.approx(1.0)
    assert report.spearman(x, x[::-1]) == pytest.approx(-1.0)
    assert report.perm_p(x, x, 1.0, n=500) < 0.01
    assert report.spearman([1, 1, 1, 1], [1, 2, 3, 4]) == 0.0           # constant input: no correlation, no crash


@pytest.mark.parametrize("text,lang,expected", [
    ("Have you ever wondered why this works?", "en", "pytanie"),
    ("Never boil your coffee again.", "en", "zakaz/ostrzeżenie"),
    ("Google właśnie udostępnił 15 narzędzi.", "pl", "liczba/wynik"),
    ("Usuwasz historię Google, ale ona nie znika.", "pl", "obietnica/trik"),
    ("Anything at all", "de", None),
    ("", "en", None),
])
def test_hook_type(text, lang, expected):
    assert report.hook_type(text, lang) == expected


# ---- page ------------------------------------------------------------------------------------------------------------
def _reel(i, views, group, mode="speech", hour=18):
    return {"id": f"r{i}", "views": views, "likes": (views or 0) // 50, "comments": (views or 0) // 200, "group": group, "mode": mode, "hour": hour,
            "weekday": i % 7, "about": f"about {i}", "hook": f"hook {i}", "dur": 30, "url": f"https://x/{i}"}


def test_analyse_finds_best_group_and_leader():
    reels = [_reel(i, 1000 + i * 10, "slow") for i in range(6)] + [_reel(10 + i, 50_000 + i * 100, "fast") for i in range(4)]
    med, scored, finds, chart, advice = page.analyse(reels)
    assert med > 0 and len(scored) == 10 and chart and chart["rows"][0]["name"] == "fast"
    assert finds[0]["big"].endswith("×") and any("fast" in f["title"] for f in finds)
    assert advice and advice[0]["items"]


def test_analyse_without_views_does_not_crash():
    reels = [_reel(i, None, "") for i in range(3)]
    med, scored, finds, chart, advice = page.analyse(reels)
    assert med is None and not scored and chart is None and finds


def test_cta_regex():
    assert "MATCH" in page.cta_of("", "Great tip.\nComment MATCH to get on the waitlist.")
    assert page.cta_of("**CTA:** Napisz INSTA", "") == "Napisz INSTA"


# ---- demo (whole pipeline without network) --------------------------------------------------------------------------
def test_demo_builds_a_page(tmp_path):
    out = tmp_path / "demo"
    r = subprocess.run([sys.executable, "-m", "reel_intel.cli", "demo", "--out", str(out), "--no-open"], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    html = (out / "Reele.html").read_text(encoding="utf-8")
    assert "demo.coffee" in html and "Quick tips for everyone" in html and "const D =" in html


# ---- Polish diacritics in OCR text ----------------------------------------------------------------------------------
def test_restore_diacritics_uses_profile_vocabulary():
    from collections import Counter
    vocab = {"moze": Counter({"może": 9}), "ze": Counter({"że": 12, "ze": 3}), "te": Counter({"te": 20, "tę": 1}), "zrobic": Counter({"zrobić": 4})}
    assert common.restore_diacritics("Moze zrobic", "pl", vocab) == "Może zrobić"
    assert common.restore_diacritics("ze mna", "pl", vocab) == "że mna"            # accented form >= 2x the plain one
    assert common.restore_diacritics("te", "pl", vocab) == "te"                    # the plain form dominates: keep
    assert common.restore_diacritics("moze", "en", vocab) == "moze"                # other languages untouched
    assert common.restore_diacritics("ŻYCIE", "pl", vocab) == "ŻYCIE"             # already has Polish letters


def test_near_duplicate_ocr_lines():
    assert common.near_duplicate("RUNNING ADS IKEA MOT", "RUNMNG ADS IKEA MOT")
    assert common.near_duplicate("What if you could tap", "What if you could tap into Meta's data")
    assert not common.near_duplicate("Bloom 30 s", "Pour to 150 g")


# ---- label-free feature analysis -------------------------------------------------------------------------------------
def test_feature_table_detects_a_real_effect_and_ignores_noise():
    import random
    from reel_intel.analysis import feature_table
    rnd = random.Random(7)
    reels = [{"views": int(rnd.uniform(800, 1200)), "dur": 10, "mode": "speech", "hook": "x", "caption": "", "hour": 10, "weekday": 1} for _ in range(15)]   # short, big
    reels = [dict(r, views=r["views"] * 20) for r in reels] + [{"views": int(rnd.uniform(800, 1200)), "dur": 60, "mode": "speech", "hook": "x", "caption": "", "hour": 10, "weekday": 1} for _ in range(15)]
    rows = {r["key"]: r for r in feature_table(reels, "en", n_boot=500)}
    assert rows["short"]["status"] == "clear" and rows["short"]["ratio"] > 10
    assert rows["evening"]["status"] == "small"                       # every video has hour 10: the 'yes' group is empty
    assert feature_table(reels[:6], "en", n_boot=200)[0]["status"] == "small"


def test_ratio_ci_brackets_the_point_estimate():
    from reel_intel.analysis import ratio_ci
    base, lo, hi = ratio_ci([10, 12, 14, 16, 18, 20], [5, 6, 7, 8, 9, 10], n_boot=500)
    assert lo <= base <= hi and base == pytest.approx(2.0, rel=0.2)


# ---- local app -------------------------------------------------------------------------------------------------------
def test_parse_sources_validates_links():
    from reel_intel.server import parse_sources
    kind, lines = parse_sources("https://youtu.be/abcDEF12345\nnot a link\njavascript:alert(1)\nhttps://x.com/a/status/1\n")
    assert kind == "urls" and lines == ["https://youtu.be/abcDEF12345", "https://x.com/a/status/1"]
    kind, lines = parse_sources("#profile\tme\nhttps://www.tiktok.com/@me/video/1\t5 tys.\nrm -rf /\tx\n")
    assert kind == "grid" and lines == ["#profile\tme", "https://www.tiktok.com/@me/video/1\t5 tys."]


def test_local_app_rejects_posts_without_token():
    import json
    import threading
    import urllib.error
    import urllib.request
    from reel_intel import server
    srv = server.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        html = urllib.request.urlopen(base + "/").read().decode()
        assert server.TOKEN in html and "{{TOKEN}}" not in html
        req = urllib.request.Request(base + "/api/run", data=json.dumps({"text": "https://youtu.be/abcDEF12345"}).encode(), method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 403
        req = urllib.request.Request(base + "/api/run", data=json.dumps({"text": "no links here"}).encode(), method="POST", headers={"X-Token": server.TOKEN})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 400
    finally:
        srv.shutdown()


# ---- MCP server ------------------------------------------------------------------------------------------------------
def test_mcp_server_lists_tools_and_runs_a_report(tmp_path):
    import json
    demo = tmp_path / "demo"
    subprocess.run([sys.executable, "-m", "reel_intel.cli", "demo", "--out", str(demo), "--no-open"], check=True, capture_output=True)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "report", "arguments": {"kind": "groups", "data_dir": str(demo)}}},
            {"jsonrpc": "2.0", "id": 4, "method": "nope"},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "report", "arguments": {}}}]
    r = subprocess.run([sys.executable, "-m", "reel_intel.mcp_server"], input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, text=True, encoding="utf-8", timeout=120)
    replies = {json.loads(l)["id"]: json.loads(l) for l in r.stdout.splitlines() if l.strip()}
    assert set(replies) == {1, 2, 3, 4, 5}                                      # the notification gets no reply
    assert replies[1]["result"]["serverInfo"]["name"] == "reel-intel"
    assert {t["name"] for t in replies[2]["result"]["tools"]} == {"scan", "report", "page", "score", "shorten", "compare"}
    assert "Quick tips for everyone" in replies[3]["result"]["content"][0]["text"] and not replies[3]["result"]["isError"]
    assert replies[4]["error"]["code"] == -32601
    assert replies[5]["result"]["isError"] and "missing argument: kind" in replies[5]["result"]["content"][0]["text"]


def test_score_runs_on_demo_data(tmp_path):
    demo = tmp_path / "demo"
    subprocess.run([sys.executable, "-m", "reel_intel.cli", "demo", "--out", str(demo), "--no-open"], check=True, capture_output=True)
    script = tmp_path / "s.txt"
    script.write_text("Stop pouring boiling water on your coffee. Wait thirty seconds. Comment BEANS for the guide.", encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "reel_intel.cli", "--data", str(demo), "score", str(script)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0 and "Script check" in r.stdout and "Most similar scanned videos" in r.stdout


# ---- insights import -------------------------------------------------------------------------------------------------
def test_insights_number_formats():
    from reel_intel.insights import number
    assert number("1 234") == 1234 and number("1,234") == 1234 and number("12,5%") == 12.5 and number("0:23") == 23 and number("1:02:03") == 3723 and number("n/a") is None


def test_insights_import_matches_rows_and_derives_ratios(tmp_path):
    import csv
    demo = tmp_path / "demo"
    subprocess.run([sys.executable, "-m", "reel_intel.cli", "demo", "--out", str(demo), "--no-open"], check=True, capture_output=True)
    f = tmp_path / "export.csv"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Post ID", "Reach", "Saves", "Shares", "Average watch time", "Likes"])
        for i in range(1, 13):
            w.writerow([f"D{i:02d}", 10000 * (13 - i), 300 + 20 * i, 100 * (13 - i), f"0:{10 + i}", 500])
    r = subprocess.run([sys.executable, "-m", "reel_intel.cli", "--data", str(demo), "insights", str(f)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0 and "matched 12 of 12" in r.stdout, r.stdout + r.stderr
    ins = json.loads((demo / "data" / "D01" / "insights.json").read_text(encoding="utf-8"))
    assert ins["shares_per_reach"] == pytest.approx(1200 / 120000) and 0 < ins["watch_ratio"] <= 1.5
    assert "shares_per_reach" in r.stdout


# ---- qa: tail gap and speaking rate -------------------------------------------------------------------------------------
def _transcript_dir(tmp_path, duration, last_end, checked):
    d = tmp_path / "r1"
    d.mkdir()
    words = [{"w": f"slowo{i}", "s": 1 + i * 0.4, "e": 1.3 + i * 0.4, "p": 0.95} for i in range(14)]
    last = round(words[-1]["e"], 2)
    tr = {"prompt_hash": common.reel_prompt({}, "pl")[1], "language": "pl", "language_probability": 0.99, "duration": duration,
          "segments": [{"start": 1, "end": last_end or last, "text": " ".join(w["w"] for w in words), "words": words}]}
    if checked:
        tr["tail_checked"] = True
    (d / "transcript.json").write_text(json.dumps(tr), encoding="utf-8")
    (d / "meta.json").write_text("{}", encoding="utf-8")
    return d


@pytest.mark.parametrize("checked,code,severity", [(False, "T_TAIL_GAP", "WARN"), (True, "T_TAIL_SILENT", "INFO")])
def test_qa_tail_gap_is_downgraded_only_after_recheck(tmp_path, checked, code, severity):
    from reel_intel import qa
    d = _transcript_dir(tmp_path, duration=40, last_end=None, checked=checked)
    out = []
    qa.check_transcript("r1", d, 40, out)
    assert [(i["code"], i["severity"]) for i in out if i["code"].startswith("T_TAIL")] == [(code, severity)]


def test_qa_speaking_rate_uses_speaking_span_not_clip_length(tmp_path):
    from reel_intel import qa
    d = _transcript_dir(tmp_path, duration=300, last_end=None, checked=True)      # 14 words in ~6 s of a 5 minute clip
    out = []
    qa.check_transcript("r1", d, 300, out)
    assert not [i for i in out if i["code"] == "T_WPM"]


# ---- benchmark metric ---------------------------------------------------------------------------------------------------
def test_benchmark_wer_and_term_matching():
    sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
    import run_benchmark as rb
    assert rb.wer("a b c d", "a b c d")[0] == 0
    assert rb.wer("a b c d", "a x c")[0] == 0.5                  # one substitution + one deletion
    assert rb.term_hit("Meta Ads", "w meta-ads widzę") and not rb.term_hit("Meta Ads", "mata adsy")


# ---- automatic short version --------------------------------------------------------------------------------------------
def _short_dir(tmp_path, text, caption="Comment GUIDE for the checklist"):
    from reel_intel import shorten
    words = text.split()
    segs = [{"start": 0.0, "end": 20.0, "text": text,
             "words": [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.3, "p": 0.95} for i, w in enumerate(words)]}]
    d = tmp_path / "v1"
    d.mkdir()
    (d / "transcript.json").write_text(json.dumps({"language": "en", "segments": segs}), encoding="utf-8")
    (d / "features.json").write_text(json.dumps({"id": "v1", "views": 1000, "likes": 5, "comments": 2, "duration_s": 20.0, "upload_date": "20260101",
                                                 "word_count": len(words), "words_per_min": 120, "speech_reliable": True, "caption": caption}), encoding="utf-8")
    return d, shorten


def test_short_version_quotes_the_recording_and_finds_cta(tmp_path):
    text = ("Most people waste money on ads. Here is the first step: define one customer. The second step is to test three hooks. "
            "Spend ten dollars a day for a week. Keep only the winner. Comment GUIDE and I will send you the checklist.")
    d, shorten = _short_dir(tmp_path, text)
    out = shorten.build(d, "en")
    assert "Hook (0-3 s):** „Most people waste money on ads.”" in out
    assert "CTA:** „Comment GUIDE and I will send you the checklist.”" in out
    key = out.split("Key sentences:**")[1].split("\n")[0]
    for quoted in key.split(" / "):                                  # every key sentence is a sentence of the transcript
        assert quoted.strip(" „”") in text


def test_short_version_never_overwrites_a_manual_file(tmp_path, monkeypatch):
    d, shorten = _short_dir(tmp_path, "One sentence here. Another one follows. And a third. And a fourth one.")
    monkeypatch.setattr(shorten, "DATA", tmp_path)
    (d / "content_short.md").write_text("written by a person", encoding="utf-8")
    shorten.run()
    assert (d / "content_short.md").read_text(encoding="utf-8") == "written by a person"
    (d / "content_short.md").unlink()
    shorten.run()
    assert shorten.AUTO in (d / "content_short.md").read_text(encoding="utf-8")
    shorten.run()                                                    # automatic files are refreshed, not skipped


# ---- language detection prefers the account's language when unsure -------------------------------------------------------
class _FakeModel:
    def __init__(self, result):
        self.result = result

    def detect_language(self, audio):
        return self.result


def _detect(monkeypatch, tmp_path, result, account, caption=None):
    monkeypatch.setattr(common, "whisper_model", lambda: _FakeModel(result))
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", type(sys)("fake_audio"))
    sys.modules["faster_whisper.audio"].decode_audio = lambda *a, **k: None
    monkeypatch.setattr(common, "account_language", lambda: account)
    return common.detect_language(tmp_path / "x.wav", caption)


def test_detect_language_unsure_prefers_account_language(monkeypatch, tmp_path):
    assert _detect(monkeypatch, tmp_path, ("cy", 0.62, [("cy", 0.62), ("en", 0.30)]), "en")[0] == "en"      # English clip heard as Welsh
    assert _detect(monkeypatch, tmp_path, ("cy", 0.62, [("cy", 0.62), ("en", 0.04)]), "en")[0] == "cy"      # English is implausible: keep the model's answer
    assert _detect(monkeypatch, tmp_path, ("de", 0.97, [("de", 0.97), ("en", 0.02)]), "en")[0] == "de"      # sure: a German video stays German
    assert _detect(monkeypatch, tmp_path, ("jw", 0.45, [("jw", 0.45), ("en", 0.30)]), None)[0] == "en"       # new account, no history: en/pl are the defaults
    assert _detect(monkeypatch, tmp_path, ("jw", 0.45, [("jw", 0.45), ("pl", 0.35), ("en", 0.05)]), None)[0] == "pl"


def test_qa_repeat_spoken_vs_loop(tmp_path):
    from reel_intel import qa

    def run(text, n, p):
        d = tmp_path / f"r{abs(hash((text, n, p)))}"
        d.mkdir()
        segs = [{"start": i * 2.0, "end": i * 2.0 + 0.5, "text": text, "words": [{"w": w, "s": i * 2.0, "e": i * 2.0 + 0.4, "p": p} for w in text.split()]} for i in range(n)]
        (d / "transcript.json").write_text(json.dumps({"prompt_hash": common.reel_prompt({}, "en")[1], "language": "en", "language_probability": 0.99,
                                                       "duration": n * 2.0, "segments": segs}), encoding="utf-8")
        (d / "meta.json").write_text("{}", encoding="utf-8")
        out = []
        qa.check_transcript("r", d, n * 2.0, out)
        return [(i["code"], i["severity"]) for i in out if i["code"] == "T_REPEAT"]

    assert run("True.", 3, 0.95) == [("T_REPEAT", "WARN")]                                   # short word, said a few times: spoken
    assert run("True.", 7, 0.95) == [("T_REPEAT", "WARN")]                                   # many times but confident: still spoken
    assert run("True.", 7, 0.4) == [("T_REPEAT", "ERROR")]                                   # many times and shaky: a loop
    assert run("this is a loop of words", 3, 0.95) == [("T_REPEAT", "ERROR")]                # a phrase repeated: a loop


def test_caption_language_and_its_use(monkeypatch, tmp_path):
    assert common.caption_language("Jacob, 21, started doing school food reviews in year 11 with his friend and now has 1.7m followers") == "en"
    assert common.caption_language("Jak zrobić automatyzację w Zapierze, żeby nie tracić czasu na to samo") == "pl"
    assert common.caption_language("#shorts #tedx #ted #truecrime #media") is None
    welsh = ("cy", 0.83, [("cy", 0.83), ("en", 0.16)])                        # English with a regional accent heard as Welsh
    assert _detect(monkeypatch, tmp_path, welsh, None, "Jacob started doing the school food reviews and now he has a lot of followers")[0] == "en"
    assert _detect(monkeypatch, tmp_path, welsh, None, "#shorts #tedx")[0] == "cy"                    # no clear caption: a real Welsh talk stays Welsh
    assert _detect(monkeypatch, tmp_path, ("de", 0.99, [("de", 0.99), ("en", 0.01)]), None, "this is the english caption of a video that you can see")[0] == "de"


def test_restore_diacritics_builtin_words_and_suffix_rules():
    assert common.restore_diacritics("Zeby nie zgubic instrukcji, przygotowac sie", "pl", common.diacritic_vocab()) == "Żeby nie zgubić instrukcji, przygotować się"
    assert common.restore_diacritics("Nauczylem sie, zaczynajacych oszczednosc", "pl", common.diacritic_vocab()) == "Nauczyłem się, zaczynających oszczędność"
    assert common.restore_diacritics("hotel balkon logic", "pl", common.diacritic_vocab()) == "hotel balkon logic"       # nothing to restore
    assert common.restore_diacritics("zeby", "en") == "zeby"                                                              # other languages untouched


# ---- skipped speech inside a segment ------------------------------------------------------------------------------------
def test_word_holes_only_inside_one_segment():
    w = lambda s: {"w": "x", "s": s, "e": s + 0.3, "p": 0.9}
    inside = [{"start": 0, "end": 30, "text": "x", "words": [w(0), w(0.4), w(26), w(26.4)]}]
    between = [{"start": 0, "end": 1, "text": "x", "words": [w(0), w(0.4)]}, {"start": 20, "end": 21, "text": "x", "words": [w(20), w(20.4)]}]
    assert [(si, wi) for si, wi, a, b in common.word_holes(inside)] == [(0, 1)]
    assert common.word_holes(between) == []                                   # silence between voice-filter chunks is normal


@pytest.mark.parametrize("checked,code,severity", [(False, "T_HOLE", "WARN"), (True, "T_HOLE_SILENT", "INFO")])
def test_qa_hole_is_downgraded_only_after_recheck(tmp_path, checked, code, severity):
    from reel_intel import qa
    d = _transcript_dir(tmp_path, duration=40, last_end=None, checked=True)
    t = json.loads((d / "transcript.json").read_text(encoding="utf-8"))
    for w in t["segments"][0]["words"][7:]:                                   # second half of the words 20 s later
        w["s"] += 20.0
        w["e"] += 20.0
    if checked:
        t["holes_checked"] = True
    (d / "transcript.json").write_text(json.dumps(t), encoding="utf-8")
    out = []
    qa.check_transcript("r1", d, 40, out)
    assert [(i["code"], i["severity"]) for i in out if i["code"].startswith("T_HOLE")] == [(code, severity)]


# ---- picture and sound (not the transcript) -----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def cut_videos(tmp_path_factory):
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("vid")
    s = "size=270x480:rate=25"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-t", "2", "-i", f"testsrc={s}", "-f", "lavfi", "-t", "2", "-i", f"mandelbrot={s}",
                    "-f", "lavfi", "-t", "2", "-i", f"smptebars={s}", "-f", "lavfi", "-t", "2", "-i", f"color=c=red:{s}", "-f", "lavfi", "-t", "2", "-i", f"color=c=green:{s}",
                    "-filter_complex", "[0:v][1:v][2:v][3:v][4:v]concat=n=5:v=1:a=0[v]", "-map", "[v]", "-pix_fmt", "yuv420p", str(d / "five.mp4")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration=10:{s}", "-pix_fmt", "yuv420p", str(d / "still.mp4")], check=True)
    return d


def test_visuals_counts_cuts_in_a_video_with_known_cuts(cut_videos):
    from reel_intel import visuals
    dur, frames = visuals.sample(cut_videos / "five.mp4")
    cuts, motion = visuals.cut_and_motion(frames)
    assert round(dur) == 10 and cuts == 4                                     # five 2-second scenes = four cuts
    dur2, frames2 = visuals.sample(cut_videos / "still.mp4")
    assert visuals.cut_and_motion(frames2)[0] == 0                            # a static picture has none


def test_visual_features_are_tested_only_where_measured():
    from reel_intel.analysis import FEATURES
    fn = {k: f for k, _, _, f in FEATURES}
    assert fn["fast_cuts"]({"cuts10": 6.0}) is True and fn["fast_cuts"]({"cuts10": 2.0}) is False and fn["fast_cuts"]({"cuts10": None}) is None
    assert fn["burned"]({"burned": None}) is None and fn["face_early"]({"face3": False}) is False
    assert fn["fast_speech"]({"wpm": 190}) is True and fn["fast_speech"]({"wpm": None}) is None


def test_ocr_agreement_ignores_spaces():
    # OCR often returns words glued together; the comparison works on 5-letter pieces of the letter stream, so that does not matter
    stream = "czylidzisiajpokazemywamjakdokladnieumycsamochod"
    ocr = "czylidzisiaj pokazemy wam jak dokladnie".replace(" ", "")
    grams = [ocr[i:i + 5] for i in range(len(ocr) - 4)]
    assert sum(g in stream for g in grams) / len(grams) == 1.0


# ---- local app: library of finished analyses ----------------------------------------------------------------------------
def test_local_app_library_lists_only_known_folders(tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from reel_intel import server
    (tmp_path / "acc1" / "data" / "x").mkdir(parents=True)
    (tmp_path / "acc1" / "data" / "x" / "features.json").write_text("{}", encoding="utf-8")
    (tmp_path / "acc1" / "Reele.html").write_text("<title>Reels @acc1</title><body>page one</body>", encoding="utf-8")
    (tmp_path / "other").mkdir()                                          # no page: not listed
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    (tmp_path / "COMPARE.md").write_text("# Cmp\n\n| a | b |\n|---|---|\n| **1** | 2 |\n", encoding="utf-8")
    server.LIBRARY = tmp_path
    srv = server.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        lib = json.loads(urllib.request.urlopen(base + "/api/library").read())
        assert [(x["id"], x["reels"]) for x in lib] == [("acc1", 1), ("_compare", 0)] and lib[0]["title"] == "Reels @acc1"
        assert b"page one" in urllib.request.urlopen(base + "/lib/acc1").read()
        assert b"<table>" in urllib.request.urlopen(base + "/lib/_compare").read()
        for bad in ("/lib/..%2Fsecret.txt", "/lib/other", "/lib/acc1%2F..%2F..", "/lib/secret.txt"):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + bad)
            assert e.value.code == 404
    finally:
        srv.shutdown()
        server.LIBRARY = None


def test_ingest_accepts_only_browser_extensions(tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from reel_intel import server
    srv = server.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/api/ingest"
    body = json.dumps({"profile": "x", "items": [{"url": "javascript:alert(1)", "views": "1"}]}).encode()

    def post(headers):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers}, method="POST")
        try:
            return urllib.request.urlopen(req).status
        except urllib.error.HTTPError as e:
            return e.code
    try:
        assert post({}) == 403                                                                   # a plain request
        assert post({"Origin": "https://evil.example", "X-Reel-Intel-Client": "extension"}) == 403   # a web page cannot pretend to be an extension
        assert post({"Origin": "chrome-extension://abcdef"}) == 403                             # extension origin but no custom header
        assert post({"Origin": "chrome-extension://abcdef", "X-Reel-Intel-Client": "extension"}) == 400   # accepted, but no valid link in it: nothing is started
        assert json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/api/ping").read())["app"] == "reel-intel"
    finally:
        srv.shutdown()


def test_library_labels_can_carry_name_and_description(tmp_path):
    from reel_intel import server
    (tmp_path / "acc1").mkdir()
    (tmp_path / "acc1" / "Reele.html").write_text("<title>t</title>", encoding="utf-8")
    (tmp_path / "labels.json").write_text(json.dumps({"acc1": {"letter": "A", "name": "@acc", "about": "what it is", "platform": "Instagram"}}), encoding="utf-8")
    server.LIBRARY = tmp_path
    try:
        item = server.library()[0]
    finally:
        server.LIBRARY = None
    assert (item["letter"], item["name"], item["about"], item["platform"], item["title"]) == ("A", "@acc", "what it is", "Instagram", "@acc")


def test_every_scan_gets_its_own_folder_and_tile(tmp_path, monkeypatch):
    from reel_intel import server
    monkeypatch.setattr(server, "WORK", tmp_path)
    assert server.job_dir(["#profile\tbmw!", "https://x"]).name == "bmw"                        # named after the profile, unsafe characters removed
    assert server.job_dir(["https://x"]).name.startswith("skan-")                                # plain links: named after the time
    assert server.job_dir(["#profile\tnasa"]) != server.job_dir(["#profile\tbmw"])              # different accounts never share a folder
    (tmp_path / "bmw").mkdir()
    (tmp_path / "bmw" / "Reele.html").write_text("<title>Reele @bmw</title>", encoding="utf-8")
    (tmp_path / "other").mkdir()                                                                 # a folder without a page is not a tile
    monkeypatch.setattr(server, "LIBRARY", None)
    assert [(x["id"], x["title"], x.get("user")) for x in server.library()] == [("scan:bmw", "@bmw", True)]


def test_repeats_must_be_consecutive_or_dense_to_count_as_a_loop():
    from reel_intel import qa
    def seg(text, start):
        return {"start": start, "end": start + 1, "text": text, "words": [{"w": w, "s": start, "e": start + 1, "p": 0.9} for w in text.split()]}
    norm = lambda segs: [" ".join(s["text"].lower().split()) for s in segs]
    scattered = [seg("to the moon", t) for t in (100, 400, 900)] + [seg(f"other words {i}", i * 10 + 5) for i in range(30)]
    scattered.sort(key=lambda s: s["start"])
    assert qa.repeat_runs(scattered, norm(scattered)) == []                              # the same phrase now and then over a long talk is speech
    loop = [seg("we will go there", t) for t in (10, 11, 12, 13)]
    assert [(r[0], r[1]) for r in qa.repeat_runs(loop, norm(loop))] == [("we will go there", 4)]       # four in a row is a loop
    dense = [seg("thank you very much", 0), seg("and then", 5), seg("thank you very much", 10), seg("so", 12), seg("thank you very much", 20), seg("ok", 22), seg("thank you very much", 30)]
    assert [(r[0], r[1]) for r in qa.repeat_runs(dense, norm(dense))] == [("thank you very much", 4)]  # four within 45 s is a loop too


def test_screen_mining_finds_repos_and_skill_names_but_not_noise():
    from reel_intel import screenmine as s
    lines = [(2.0, "github.com/anthropics/claude-code"), (5.0, "npx skills add"), (5.5, "vercel-labs/agent-skills"), (8.0, "12/10 wynik 3/4"),
             (9.0, "/plugin marketplace add"), (9.5, "frontend-design   SKILL.md"), (12.0, "GitHub / obra / superpowers"), (20.0, "and/or www.instagram.com/nasa/reel"),
             (40.0, "tail-tisk"), (41.0, "capital-expansion")]
    m = s.mine(lines, ["Claude Code", "n8n"])
    assert [r["repo"] for r in m["repos"]] == ["anthropics/claude-code", "obra/superpowers", "vercel-labs/agent-skills"] or \
        {r["repo"] for r in m["repos"]} == {"anthropics/claude-code", "obra/superpowers", "vercel-labs/agent-skills"}
    toks = {x["token"] for x in m["tokens"]}
    assert {"/plugin", "SKILL.md", "frontend-design"} <= toks                       # skill-like, with a hint word on the screen
    assert "tail-tisk" not in toks and "capital-expansion" not in toks              # random OCR words far from any hint
    assert [k["name"] for k in m["known"]] == ["Claude Code"]


def test_screen_report_route_serves_only_known_folders(tmp_path, monkeypatch):
    import threading
    import urllib.error
    import urllib.request
    from reel_intel import server
    (tmp_path / "bmw").mkdir()
    (tmp_path / "bmw" / "Reele.html").write_text("<title>x</title>", encoding="utf-8")
    (tmp_path / "bmw" / "NA_EKRANIE.md").write_text("# Co pokazano na ekranie\n\n- `obra/superpowers`: w filmach A (1x)\n", encoding="utf-8")
    monkeypatch.setattr(server, "WORK", tmp_path)
    monkeypatch.setattr(server, "LIBRARY", None)
    srv = server.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        body = urllib.request.urlopen(base + "/screen?lib=scan:bmw").read().decode("utf-8")
        assert "obra/superpowers" in body and "<title>Co pokazano na ekranie</title>" in body
        for bad in ("/screen?lib=..%2F..%2Fx", "/screen?lib=scan:nope"):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + bad)
            assert e.value.code == 404
    finally:
        srv.shutdown()
