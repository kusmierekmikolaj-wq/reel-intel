"""verdict.py - from the findings of a check to ONE answer a person can act on: gotowe / z uwagami / niegotowe, an exit code, and a report in Polish.

  gotowe       nothing above INFO was found: the reel can go out (after a human listens: this system does not judge taste)
  z uwagami    only WARNs: nothing broken, but something a person should look at (the report says what, where and what was already repaired)
  niegotowe    at least one ERROR: do not publish this file
Exit codes follow aayush0124/reel-qc: 0 gotowe, 10 z uwagami, 1 niegotowe, 2 the check itself could not run (no file, no sidecar, a crash).

A WARN never turns into "niegotowe" by counting: ten warnings are still "z uwagami". What counts as an ERROR is decided per code in the registry (base.REGISTRY)
and per finding by the detector (a scene where most words are wrong is an ERROR, a few are a WARN).
"""
from collections import Counter

from .base import REGISTRY

READY, NOTES, NOT_READY = "gotowe", "z uwagami", "niegotowe"
EXIT = {READY: 0, NOTES: 10, NOT_READY: 1, "error": 2}
ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2}

# a short name in Polish for every code (a test makes sure that no code of the registry is missing here)
LABELS_PL = {
    "AUDIO_RATE": "częstotliwość dźwięku inna niż 44,1/48 kHz",
    "AV_LENGTH": "obraz i dźwięk mają różną długość",
    "AV_OFFSET": "dźwięk startuje inaczej niż obraz w pliku",
    "CAPTION_SPEED": "napisy za szybkie lub za krótko na ekranie",
    "CAPTION_SYNC": "napisy rozjechane z mową",
    "CONTRAST": "za mały kontrast tekstu z tłem",
    "EARS_OFF": "model słuchu nie działał (oddechy oceniane tylko po poziomie)",
    "EAR_BREATH": "model słuchu słyszy oddech lub dźwięk ciała",
    "ENGINE_FALLBACK": "cicha zamiana głosu na zapasowy silnik",
    "FILE_BAD": "plik nie daje się odczytać",
    "FIRST_FRAME": "martwa pierwsza klatka (pusta, czarna lub jednolita)",
    "FLICKER": "błyski obrazu (więcej niż 3 na sekundę)",
    "GAP_CLICK": "trzask lub skok w dźwięku między zdaniami",
    "GAP_LONG": "za długa przerwa między zdaniami",
    "GAP_LOUD": "za głośny dźwięk w przerwie między zdaniami",
    "GAP_RUMBLE": "dudnienie (niskie tony) w przerwie między zdaniami",
    "GAP_SHORT": "zdania zlewają się, za krótka przerwa",
    "LIPSYNC": "usta maskotki nie nadążają za głosem",
    "LOUDNESS": "głośność całości poza celem",
    "MIX_CLIP": "przesterowanie dźwięku",
    "MIX_DROPOUT": "dziura (zanik dźwięku) w trakcie mowy",
    "MIX_PUMPING": "muzyka pompuje (głośność faluje z mową)",
    "MIX_VOICE_TO_MUSIC": "muzyka za głośna względem głosu",
    "NO_AUDIO": "plik bez ścieżki dźwiękowej",
    "OCR_OFF": "nie sprawdzono pozycji tekstu (brak OCR)",
    "PACE": "średnie tempo mowy (informacja)",
    "QA_ERROR": "błąd detektora (usterka skanera, nie rolki)",
    "SIDECAR_MISSING": "brak pliku .qa.json obok rolki: nie znam osi czasu",
    "SIDECAR_OLD": "plik .qa.json bez sumy kontrolnej (starsza wersja)",
    "SIDECAR_STALE": "plik .qa.json pochodzi z innej wersji rolki (suma kontrolna się różni)",
    "PICTURE_BLACK": "czarne klatki",
    "PICTURE_ERROR": "błąd detektora obrazu (usterka narzędzia, nie rolki)",
    "PICTURE_FREEZE": "zamrożony obraz podczas mowy",
    "PICTURE_OFF": "nie sprawdzono obrazu (brak klatek)",
    "PLATFORM_ASPECT": "obraz nie jest 9:16",
    "PLATFORM_CODEC": "kodek inny niż H.264/AAC",
    "PLATFORM_DURATION": "długość rolki poza dopuszczalnym oknem",
    "PLATFORM_FASTSTART": "plik nie startuje w trakcie pobierania (moov na końcu)",
    "PLATFORM_FPS": "klatkaż inny niż 30 fps",
    "PLATFORM_PIXFMT": "format pikseli inny niż yuv420p",
    "PLATFORM_SIZE": "rozdzielczość inna niż 1080x1920",
    "RENDER_FALLBACK": "uproszczony renderer zamiast animowanego (bez maskotki)",
    "SAFE_ZONE": "tekst lub pasek poza strefą bezpieczną",
    "TEXT_LEGIBILITY": "za małe litery",
    "TEXT_OVERLAP": "teksty nachodzą na siebie lub na brzeg karty",
    "TRANSITION_GHOST": "dwie sceny naraz na ekranie przy przejściu",
    "TRUE_PEAK": "szczyt sygnału (true peak) powyżej limitu",
    "VIDEO_FACTS": "dane o pliku (informacja)",
    "VOICE_BREATH": "oddech, szmer lub oddechowy ogon w głosie",
    "VOICE_BURST": "wyścig: nagle za szybki fragment zdania",
    "VOICE_CUT": "ucięte ostatnie słowo",
    "VOICE_DEL": "pominięte słowo ze skryptu",
    "VOICE_DRIFT": "głos zmienia się między scenami",
    "VOICE_DRONE": "przeciągnięta ostatnia samogłoska",
    "VOICE_FAST": "za szybkie tempo mowy",
    "VOICE_HISS": "szum pod głosem",
    "VOICE_INS": "dodane lub powtórzone słowo",
    "VOICE_LEVEL": "skok głośności głosu między scenami",
    "VOICE_MONOTONE": "monotonna intonacja",
    "VOICE_NOISE": "szum, trzaski lub kliknięcia pod głosem",
    "VOICE_PAUSE_INNER": "pauza w środku zdania",
    "VOICE_PITCH_JUMP": "skok wysokości głosu (usterka syntezy)",
    "VOICE_SLOW": "za wolne tempo mowy",
    "VOICE_SUB": "słowo wymówione jak inne (zła wymowa)",
    "VOICE_TAIL": "cisza po ostatnim słowie (przycięta)",
}
ACTIONS_PL = {
    "retake_scene": "nagrano zdanie od nowa",
    "respell": "zmieniono zapis wymowy słowa i nagrano od nowa",
    "slow_scene": "zwolniono tempo zdania",
    "retrim_scene": "przycięto koniec zdania",
    "level_scenes": "wyrównano głośność scen",
    "remix": "przemiksowano muzykę",
    "master": "ustawiono głośność końcową (mastering)",
    "relayout": "zmieniono układ obrazu",
    "rerender": "wyrenderowano od nowa",
}

# findings that only say what could NOT be checked (the report lists them apart: they are not faults)
NOT_CHECKED = ("PICTURE_OFF", "OCR_OFF", "EARS_OFF", "SIDECAR_OLD")


def label(code):
    return LABELS_PL.get(code) or REGISTRY.get(code, {}).get("desc") or code


def blocking(findings):
    """the findings that make a reel not ready or ready-with-notes: ERROR and WARN"""
    return [f for f in findings if f.sev in ("ERROR", "WARN")]


def decide(findings):
    if any(f.sev == "ERROR" for f in findings):
        return NOT_READY
    if any(f.sev == "WARN" for f in findings):
        return NOTES
    return READY


def exit_code(verdict):
    return EXIT.get(verdict, 2)


def summarize(findings, history=None, checked=None, notes_report=None):
    """the verdict and the numbers behind it as plain JSON-able data (stored in <reel>.qa.json next to the sidecar)"""
    v = decide(findings)
    by = Counter(f.code for f in blocking(findings))
    out = {"verdict": v, "exit": exit_code(v), "errors": sum(1 for f in findings if f.sev == "ERROR"), "warnings": sum(1 for f in findings if f.sev == "WARN"),
           "info": sum(1 for f in findings if f.sev == "INFO"), "codes": dict(sorted(by.items())),
           "scenes": sorted({f.scene for f in blocking(findings) if f.scene}),
           "not_checked": sorted({f.code for f in findings if f.code in NOT_CHECKED}),
           "findings": [{"sev": f.sev, "code": f.code, "scene": f.scene, "t": None if f.t is None else round(float(f.t), 2), "msg": f.msg} for f in
                        sorted(findings, key=lambda f: (ORDER.get(f.sev, 3), f.scene or 0, f.t or 0))]}
    if history:
        out["rounds"] = [{"round": r["round"], "before": r.get("before"), "actions": [{"kind": a["kind"], "scene": a.get("scene"), "ok": a.get("ok")} for a in r["actions"]],
                          "after": r.get("after")} for r in history]
    if checked:
        out["checked"] = checked
    if notes_report:
        out["notes"] = notes_report
    return out


def _where(f):
    bits = []
    if f.scene:
        bits.append(f"scena {f.scene}")
    if f.t is not None:
        bits.append(f"{float(f.t):.1f} s")
    return ", ".join(bits)


def report_pl(findings, history=None, title="Kontrola rolki", notes_report=None, uncertain=True):
    """the report as Markdown in Polish: verdict, what was found (ERROR first), what was repaired and what stays, what was not checked, what only a person can judge"""
    v = decide(findings)
    sign = {READY: "✔", NOTES: "!", NOT_READY: "✘"}[v]
    out = [f"# {title}", "", f"**Werdykt: {v}** {sign}", ""]
    errs = [f for f in findings if f.sev == "ERROR"]
    warns = [f for f in findings if f.sev == "WARN"]
    out.append(f"Znaleziono: {len(errs)} błędów, {len(warns)} uwag." + (" Nic poważniejszego niż informacje." if not errs and not warns else ""))
    out.append("")
    for name, group in (("Błędy (rolki nie wolno publikować, dopóki zostają)", errs), ("Uwagi (do obejrzenia i odsłuchania)", warns)):
        if not group:
            continue
        out += [f"## {name}", "", "| co | gdzie | opis |", "|---|---|---|"]
        for f in sorted(group, key=lambda f: (f.scene or 0, f.t or 0)):
            out.append(f"| {label(f.code)} (`{f.code}`) | {_where(f) or 'cała rolka'} | {f.msg.replace('|', '/')} |")
        out.append("")
    if history:
        out += ["## Naprawy automatyczne", ""]
        for r in history:
            acts = "; ".join(f"{ACTIONS_PL.get(a['kind'], a['kind'])}" + (f" (scena {a['scene']})" if a.get("scene") else "") + ("" if a.get("ok", True) else " — NIE UDAŁO SIĘ") for a in r["actions"])
            out.append(f"- runda {r['round']}: {acts or 'brak działań'}; błędów/uwag przed: {r.get('before')}, po: {r.get('after')}")
        out.append("")
    info = [f for f in findings if f.sev == "INFO"]
    skipped = [f for f in info if f.code in NOT_CHECKED]
    if skipped:
        out += ["## Czego nie sprawdzono", ""] + [f"- {label(f.code)}: {f.msg}" for f in skipped] + [""]
    if notes_report:
        out += ["## Zgodność z uwagami człowieka", "", notes_report if isinstance(notes_report, str) else str(notes_report), ""]
    out += ["## Czego ten system nie ocenia", "",
            "- Czy głos brzmi naturalnie i przyjemnie, czy wymowa jest dobra w całości, czy muzyka się podoba i czy żart działa. Skaner mierzy to, co da się zmierzyć, i zgłasza kandydatów; "
            "ostatni odsłuch należy do człowieka.",
            "- Rozpoznawanie mowy samo się myli (często gubi pierwsze słowo pliku), więc uwagi o słowach to wskazówki, nie wyrok."]
    if uncertain:
        out.append("- Strefy bezpieczne Reels, docelowa głośność i progi napisów pochodzą ze źródeł, które się różnią; są konfigurowalne i oznaczone jako niepewne.")
    out.append("")
    return "\n".join(out)


def format_console(findings, history=None):
    """a compact text for the terminal: one line per finding, then the verdict"""
    lines = []
    for f in sorted(findings, key=lambda f: (ORDER.get(f.sev, 3), f.scene or 0, f.t or 0)):
        if f.sev == "INFO" and f.code not in NOT_CHECKED and f.code not in ("PACE", "LOUDNESS"):
            continue
        lines.append(f"{f.sev:5s} {f.code:18s} {f.msg}")
    if history:
        for r in history:
            lines.append(f"naprawa, runda {r['round']}: " + "; ".join(f"{a['kind']}" + (f"#{a['scene']}" if a.get("scene") else "") + ("" if a.get("ok", True) else " (nie udało się)") for a in r["actions"]) +
                         f" -> {r.get('after')}")
    v = decide(findings)
    e = sum(1 for f in findings if f.sev == "ERROR")
    w = sum(1 for f in findings if f.sev == "WARN")
    lines.append(f"werdykt: {v.upper()} ({e} błędów, {w} uwag)")
    return "\n".join(lines)
