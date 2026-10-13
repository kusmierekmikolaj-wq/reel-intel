"""page.py - a readable, searchable page for ANY set of scanned reels (any profile, any topic, Polish or English interface).

  reel.py page [--fragment]     -> <data>/Reele.html  (+ page.fragment.html for publishing as a web page artifact)
Interface language: `ui_language: pl|en` in <data>/config.yaml (default pl). Everything shown is computed from the data folder:
headline numbers, topic chart (needs topics.csv), timing and comment checks, advice. Nothing is specific to one creator.
Reels that could not be downloaded are read from data/<id>/page_scrape.json
({"id","url","views","likes","comments","date","dur","caption","about","cta"}).
"""
import base64
import datetime as dt
import json
import re
import statistics as st
from pathlib import Path

from .common import CFG, CODE, DATA, WORK, apply_fixes, jload, near_duplicate, restore_diacritics, visual_fields
from .analysis import feature_table
from .report import perm_p, spearman

# the tab icon, embedded so that the page works as a single saved file; it must sit in <head>, not in <body>
ICON_LINK = '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,' + base64.b64encode((CODE / "logo.svg").read_bytes()).decode() + '">'

LANG = CFG.get("ui_language", "pl") if CFG.get("ui_language") in ("pl", "en") else "pl"
DEC, K, M = (",", " tys.", " mln") if LANG == "pl" else (".", "k", "M")


def L(pl, en):
    return en if LANG == "en" else pl


UI = {
    "pl": {
        "skip": "Przejdź do listy reeli", "h_find": "Najważniejsze w minutę",
        "note": "To wnioski z jednego profilu i ograniczonej liczby reeli, więc traktuj je jako wskazówki, nie reguły. Instagram nie pokazuje publicznie, ile osób oglądało do końca ani ile razy reel wysłano znajomym.",
        "h_topics": "Który temat działa najlepiej", "topics_intro": "Typowy wynik (mediana wyświetleń) w grupach tematów.",
        "chart_small": "mediana {m} · {x} typowego · {n} reeli", "h_advice": "Co z tego wynika dla Twoich reeli", "h_list": "Wszystkie reele",
        "list_intro": "Znajdź reel po słowie z jego tekstu, opisu albo z ekranu. Kliknij „Pełny tekst”, żeby przeczytać, co dokładnie w nim padło.",
        "search_ph": "Szukaj słowa w tekstach", "search_label": "Szukaj w tekstach reeli", "sort_label": "Kolejność",
        "sort_views": "Najwięcej wyświetleń", "sort_new": "Najnowsze", "sort_old": "Najstarsze", "sort_comments": "Najwięcej komentarzy", "sort_long": "Najdłuższe",
        "chips_label": "Temat", "chip_all": "Wszystkie ({n})", "count": "Pokazano {n} z {m} reeli", "expand": "Rozwiń wszystkie teksty", "collapse": "Zwiń wszystkie teksty",
        "empty": "Nic nie pasuje do wybranych filtrów.", "reset": "Wyczyść filtry", "views_unit": "wyświetleń", "views_unknown": "wyświetleń: nieznane",
        "typical": "{x} typowego", "v_cuts": "{n} cięć/10 s", "v_fast": "szybki montaż", "v_face": "twarz na początku", "v_burned": "napisy w obrazie", "v_text": "tekst na początku", "tag_visual": "bez mowy: tekst na ekranie", "tag_blocked": "tylko opis i liczby", "cta": "Wezwanie do działania:", "none": "brak",
        "lc": "{l} lajków · {c} komentarzy", "full": "Pełny tekst", "sec_speech": "Co mówi autor", "sec_screen": "Tekst na ekranie", "sec_caption": "Opis posta",
        "sec_note": "Uwaga", "blocked_note": "Brak transkrypcji: ten reel wymaga zalogowania, którego program nie może użyć. Poniżej opis posta.",
        "copy": "Kopiuj tekst", "copied": "Skopiowano", "copy_fb": "Zaznaczono: naciśnij Ctrl+C", "open": "Otwórz oryginał", "unknown": "brak", "sec_frames": "Klatki z reela",
        "h_feat": "Co się wiąże z zasięgiem", "feat_intro": "Porównanie typowego wyniku reeli z daną cechą i bez niej. Różnicę uznajemy za wyraźną tylko wtedy, gdy przedział 99% (ostrzejszy, bo sprawdzamy kilka cech naraz) nie obejmuje 1 i obie grupy mają co najmniej 5 reeli. Cechy często się nakładają (np. krótkie reele to zwykle te bez mowy), więc nie sumuj ich efektów.",
        "f_feature": "Cecha", "f_with": "Z cechą (mediana, n)", "f_without": "Bez cechy (mediana, n)", "f_ratio": "Stosunek (przedział 99%)", "f_verdict": "Wniosek",
        "v_clear": "wyraźna różnica", "v_unclear": "brak wyraźnej różnicy", "v_small": "za mało reeli",
        "h_topics_auto": "Jaki format działa najlepiej", "topics_intro_auto": "Typowy wynik (mediana wyświetleń) w grupach według długości reela. Podział na tematy dodasz w pliku topics.csv.",
    },
    "en": {
        "skip": "Skip to the reel list", "h_find": "The essentials in a minute",
        "note": "These conclusions come from one profile and a limited number of reels, so treat them as hints, not rules. Instagram does not publicly show how many people watched to the end or how often a reel was sent to friends.",
        "h_topics": "Which topic works best", "topics_intro": "Typical result (median views) per topic group.",
        "chart_small": "median {m} · {x} typical · {n} reels", "h_advice": "What this means for your reels", "h_list": "All reels",
        "list_intro": "Find a reel by a word from its speech, caption or on-screen text. Click “Full text” to read exactly what was said.",
        "search_ph": "Search words in the texts", "search_label": "Search the reel texts", "sort_label": "Order",
        "sort_views": "Most views", "sort_new": "Newest", "sort_old": "Oldest", "sort_comments": "Most comments", "sort_long": "Longest",
        "chips_label": "Topic", "chip_all": "All ({n})", "count": "Showing {n} of {m} reels", "expand": "Expand all texts", "collapse": "Collapse all texts",
        "empty": "Nothing matches the selected filters.", "reset": "Clear filters", "views_unit": "views", "views_unknown": "views: unknown",
        "typical": "{x} typical", "v_cuts": "{n} cuts/10 s", "v_fast": "fast editing", "v_face": "face at the start", "v_burned": "burned-in captions", "v_text": "text at the start", "tag_visual": "no speech: on-screen text", "tag_blocked": "caption and numbers only", "cta": "Call to action:", "none": "none",
        "lc": "{l} likes · {c} comments", "full": "Full text", "sec_speech": "What the author says", "sec_screen": "On-screen text", "sec_caption": "Post caption",
        "sec_note": "Note", "blocked_note": "No transcript: this reel needs a login the program cannot use. The post caption is below.",
        "copy": "Copy text", "copied": "Copied", "copy_fb": "Selected: press Ctrl+C", "open": "Open original", "unknown": "unknown", "sec_frames": "Frames from the reel",
        "h_feat": "What goes with reach", "feat_intro": "Typical views of videos with a feature compared with those without it. A difference counts as clear only when the 99% interval (stricter because several features are tested at once) excludes 1 and both groups have at least 5 videos. Features often overlap (e.g. short videos are usually the ones without speech), so do not add their effects.",
        "f_feature": "Feature", "f_with": "With (median, n)", "f_without": "Without (median, n)", "f_ratio": "Ratio (99% range)", "f_verdict": "Verdict",
        "v_clear": "clear difference", "v_unclear": "no clear difference", "v_small": "too few videos",
        "h_topics_auto": "Which format works best", "topics_intro_auto": "Typical result (median views) per reel length. Add a topic split in topics.csv.",
    },
}


def num(x, d=1):
    return f"{x:.{d}f}".replace(".", DEC)


def compact(n):
    if n is None:
        return L("brak", "unknown")
    return num(n / 1e6) + M if n >= 1e6 else f"{round(n / 1e3)}{K}" if n >= 1e5 else num(n / 1e3) + K if n >= 1e3 else str(int(n))


def times(x):
    return (str(round(x)) if x >= 10 else num(x)) + "×"


def after(label, short):
    m = re.search(r"\*\*" + re.escape(label) + r"[^*]*\*\*:?\s*(.+)", short)
    return m.group(1).strip() if m else None


def clean_md(s):
    return re.sub(r"\s*\*\(odczyt OCR[^)]*\)\*", "", s or "").replace("`", "").strip()


def about_of(short):
    a = after("O czym:", short) or after("About:", short)
    if a:
        return clean_md(a)
    m = re.search(r"## (?:O czym|About)[^\n]*\n(.+)", short)
    return clean_md(m.group(1)) if m else ""


CTA_RE = r"((?:Skomentuj|Napisz|Zostaw|Zapisz|Comment|Write|Type|Drop|Send|DM|Reply|Tell me)[^.!?\n]{3,90})"


def cta_of(short, caption):
    c = after("CTA:", short)
    if c:
        return clean_md(c)
    m = re.search(CTA_RE, caption or "")
    return clean_md(m.group(1)) if m else L("brak", "none")


def trim_words(s, n=140):
    """shorten at a word boundary with an ellipsis; never in the middle of a word"""
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0].rstrip(",;:-") + "…"


def first_sentence(text):
    s = re.split(r"(?<=[.?!])\s", text.strip())[0] if text else ""
    return trim_words(s, 160)


def date_fmt(d):
    return f"{d[6:8]}.{d[4:6]}.{d[:4]}" if d and len(d) == 8 else (d or "")


def bucket(dur):
    """automatic grouping by length when no topics.csv exists, so the chart works without any manual labelling"""
    return L("Krótkie (do 15 s)", "Short (up to 15 s)") if dur <= 15 else L("Średnie (16-45 s)", "Medium (16-45 s)") if dur <= 45 else L("Długie (ponad 45 s)", "Long (over 45 s)")


def sheet_uri(d):
    """contact sheet of a reel without speech as a small inline JPEG, so the card shows the picture next to the text"""
    f = d / "contact_sheet_1.jpg"
    if not f.exists():
        return ""
    try:
        import base64
        import cv2
        img = cv2.imread(str(f))
        w = 720
        img = cv2.resize(img, (w, int(img.shape[0] * w / img.shape[1])))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 68])
        return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode() if ok else ""
    except Exception:
        return ""


def load_reels():
    topics = {}
    if (WORK / "topics.csv").exists():
        topics = dict(l.split(",", 1) for l in (WORK / "topics.csv").read_text(encoding="utf-8").splitlines()[1:] if "," in l)
    out = []
    for d in sorted(DATA.iterdir()):
        if (d / "features.json").exists():
            f = jload(d / "features.json")
            tr = jload(d / "transcript.json")
            short = (d / "content_short.md").read_text(encoding="utf-8") if (d / "content_short.md").exists() else ""
            speech_ok = f.get("word_count", 0) >= 10 and f.get("speech_reliable", True)
            speech = apply_fixes(" ".join(s["text"] for s in tr["segments"]), tr["language"])[0] if speech_ok else ""
            screen = ""
            if not speech_ok:
                screen = clean_md(after("Treść:", short) or after("Content:", short) or "")
                if not screen and (d / "onscreen.json").exists():
                    seen = []
                    for o in jload(d / "onscreen.json"):
                        for t in o["text"]:
                            t = restore_diacritics(t, tr["language"])
                            if not any(near_duplicate(t, x) for x in seen):
                                seen.append(t)
                    screen = " / ".join(seen)
            out.append({"id": f["id"], "url": f["url"], "views": f.get("views"), "likes": f.get("likes"), "comments": f.get("comments"),
                        "dur": round(f["duration_s"]), "date": date_fmt(f.get("upload_date")), "hour": f.get("hour"), "weekday": f.get("weekday"),
                        "group": topics.get(f["id"], ""), "about": about_of(short) or first_sentence(speech) or trim_words(f.get("caption") or "", 120),
                        "cta": cta_of(short, f.get("caption")), "hook": first_sentence(speech), "speech": speech, "screen": screen,
                        "caption": f.get("caption") or "", "mode": "speech" if speech_ok else "visual", "who": f.get("uploader") or "",
                        "sheet": "" if speech_ok else sheet_uri(d), **visual_fields(f)})
        elif (d / "page_scrape.json").exists():
            p = jload(d / "page_scrape.json")
            out.append({"id": p["id"], "url": p["url"], "views": p.get("views"), "likes": p.get("likes"), "comments": p.get("comments"),
                        "dur": p.get("dur", 0), "date": p.get("date", ""), "hour": None, "weekday": None, "group": topics.get(p["id"], ""),
                        "about": p.get("about") or trim_words(p.get("caption") or "", 120), "cta": p.get("cta") or L("brak", "none"), "hook": "", "speech": "",
                        "screen": "", "caption": p.get("caption") or "", "mode": "blocked", "who": "", "sheet": ""})
    if out and not topics:                                       # no manual topics: group by length
        for r in out:
            r["group"] = bucket(r["dur"])
    return out


def analyse(reels, auto=False):
    scored = [r for r in reels if r["mode"] != "blocked" and r["views"]]
    if not scored:                                               # links only, no view counts yet: still a readable text library
        return None, scored, [{"big": L("brak liczb", "no numbers"), "title": L("Nie znamy liczby wyświetleń", "View counts are unknown"),
                               "text": L("Bez wyświetleń nie da się porównać reeli. Dodaj je poleceniem „scan --grid” (skill collect-reel-links) i odśwież stronę.",
                                         "Without views the reels cannot be compared. Add them with “scan --grid” (skill collect-reel-links) and refresh the page.")}], None, \
               [{"text": L("Przeczytaj teksty poniżej i zaznacz, które reele wyróżniają się tematem lub pierwszym zdaniem.",
                           "Read the texts below and mark which reels stand out by topic or first sentence.")}]
    med = st.median(r["views"] for r in scored)
    leader = max(scored, key=lambda r: r["views"])
    finds, advice, chart = [], [], None

    finds.append({"big": times(leader["views"] / med), "title": L(f"Najlepszy reel: {compact(leader['views'])} wyświetleń", f"Best reel: {compact(leader['views'])} views"),
                  "text": L(f"To {times(leader['views'] / med)} typowego reela tego profilu ({compact(med)}). O czym jest: {leader['about']}",
                            f"That is {times(leader['views'] / med)} the typical reel of this profile ({compact(med)}). What it is about: {leader['about']}")})

    groups = {}
    for r in scored:
        if r["group"]:
            groups.setdefault(r["group"], []).append(r)
    stats = sorted(((g, len(v), st.median(x["views"] for x in v)) for g, v in groups.items() if len(v) >= 3), key=lambda t: -t[2])
    best = None
    if len(stats) >= 2:
        best = stats[0]
        wo = [x["views"] for x in groups[best[0]] if x["id"] != leader["id"]]
        wo_txt = L(f" Bez najlepszego reela nadal {times(st.median(wo) / med)}.", f" Without the best reel still {times(st.median(wo) / med)}.") \
            if len(wo) >= 3 and leader["group"] == best[0] else ""
        if best[2] / med >= 1.5:
            finds.append({"big": times(best[2] / med), "title": L(f"Najlepiej działa {'format' if auto else 'temat'}: {best[0]}", f"Best-working {'format' if auto else 'topic'}: {best[0]}"),
                          "text": L(f"Typowy reel z tej grupy ma {times(best[2] / med)} więcej wyświetleń niż typowy reel profilu.{wo_txt}",
                                    f"The typical reel in this group has {times(best[2] / med)} the views of the profile's typical reel.{wo_txt}")})
        else:
            finds.append({"big": L("brak lidera", "no leader"), "title": L("Żadna grupa się nie wyróżnia", "No group stands out"),
                          "text": L("Typowe wyniki grup są podobne. Zasięg zależy tu od czegoś innego.",
                                    "Typical results of the groups are similar. Reach here depends on something else.")})
        chart = {"rows": [{"name": g, "n": n, "med": m, "x": m / med} for g, n, m in stats], "median": med,
                 "legend": L(f"Typowy reel profilu: {compact(med)} wyświetleń. Grupy mają co najmniej 3 reele.",
                             f"The profile's typical reel: {compact(med)} views. Groups have at least 3 reels.")}
    else:
        finds.append({"big": L("etykiety tematów", "topic labels"), "title": L("Który temat działa najlepiej?", "Which topic works best?"),
                      "text": L("Żeby to policzyć, przypisz reele do tematów (plik topics.csv). Poproś o to Claude'a.",
                                "To work this out, assign the reels to topics (file topics.csv). Ask Claude to do it.")})

    timed = [r for r in scored if r["hour"] is not None]
    if len(timed) >= 10:
        v = [r["views"] for r in timed]
        ph = perm_p([r["hour"] for r in timed], v, spearman([r["hour"] for r in timed], v))
        pw = perm_p([r["weekday"] for r in timed], v, spearman([r["weekday"] for r in timed], v))
        if min(ph, pw) >= 0.05:
            finds.append({"big": L("bez znaczenia", "no effect"), "title": L("Godzina publikacji nic nie zmienia", "Posting time changes nothing"),
                          "text": L(f"W tej próbie ({len(timed)} reeli) nie ma związku między godziną ani dniem tygodnia a zasięgiem.",
                                    f"In this sample ({len(timed)} reels) there is no link between the hour or weekday and reach.")})
        else:
            finds.append({"big": L("ma znaczenie", "matters"), "title": L("Termin publikacji coś zmienia", "Posting time changes something"),
                          "text": L(f"W tej próbie ({len(timed)} reeli) godzina lub dzień tygodnia wiążą się z zasięgiem (p={num(min(ph, pw), 2)}). Sprawdź to na większej próbie.",
                                    f"In this sample ({len(timed)} reels) the hour or weekday is linked to reach (p={num(min(ph, pw), 2)}). Check it on a larger sample.")})

    cr = [r for r in scored if r["comments"] is not None]
    if len(cr) >= 10:
        rate = [r["comments"] / r["views"] for r in cr]
        v = [r["views"] for r in cr]
        rho = spearman(rate, v)
        p = perm_p(rate, v, rho)
        lr = leader["comments"] / leader["views"] * 100 if leader["comments"] is not None else None
        extra = L(f" Najlepszy reel ma {num(lr, 2)}% komentarzy na wyświetlenie, typowy {num(st.median(rate) * 100, 2)}%.",
                  f" The best reel has {num(lr, 2)}% comments per view, the typical one {num(st.median(rate) * 100, 2)}%.") if lr is not None else ""
        if p >= 0.05:
            finds.append({"big": L("komentarze ≠ zasięg", "comments ≠ reach"), "title": L("Dużo komentarzy nie oznacza dużego zasięgu", "Many comments do not mean wide reach"),
                          "text": L("Odsetek komentarzy na wyświetlenie nie zapowiada, który reel dotrze daleko. Prośba o słowo w komentarzu zbiera chętnych.",
                                    "The share of comments per view does not predict which reel travels far. Asking for a keyword in the comments collects interested people.") + extra})
        else:
            finds.append({"big": L("komentarze ↔ zasięg", "comments ↔ reach"), "title": L("Komentarze wiążą się z zasięgiem", "Comments are linked to reach"),
                          "text": L(f"Reele z wyższym odsetkiem komentarzy mają {'większy' if rho > 0 else 'mniejszy'} zasięg (p={num(p, 2)}).{extra}",
                                    f"Reels with a higher share of comments have {'wider' if rho > 0 else 'narrower'} reach (p={num(p, 2)}).{extra}")})

    pool = groups[best[0]] if best and best[2] / med >= 1.5 else scored
    ex = sorted(pool, key=lambda r: -r["views"])[:3]
    advice.append({"text": (L(f"Wzoruj się na grupie „{best[0]}”. Najlepsze przykłady:", f"Model your reels on the group “{best[0]}”. Best examples:")
                            if pool is not scored else L("Zacznij od najlepszych reeli profilu:", "Start with the best reels of the profile:")),
                   "items": [f"{compact(r['views'])}: {r['about']}" for r in ex]})
    hooks = [r["hook"] for r in sorted(scored, key=lambda r: -r["views"]) if r["hook"]][:3]
    if hooks:
        advice.append({"text": L("Wejdź od razu w konkret. Tak zaczynały się najlepsze reele z mową:", "Go straight to the point. This is how the best reels with speech started:"), "items": hooks})
    advice.append({"text": L("Nie kopiuj cudzych nagrań ani tekstów. Weź wzorzec: temat, pierwsze zdanie, kroki i prośbę na końcu, i napisz własną wersję.",
                             "Do not copy other people's recordings or texts. Take the pattern: topic, first sentence, steps and the ask at the end, and write your own version.")})
    return med, scored, finds, chart, advice


def main():
    reels = load_reels()
    auto = bool(reels) and not (WORK / "topics.csv").exists()
    med, scored, finds, chart, advice = analyse(reels, auto)
    handles = re.findall(r"instagram\.com/([\w.]+)/reel", " ".join(r["url"] for r in reels))
    whos = [r["who"] for r in reels if r.get("who")]
    handle = max(set(handles), key=handles.count) if handles else (max(set(whos), key=whos.count).replace(" ", "") if whos else L("profil", "profile"))
    nb = sum(r["mode"] == "blocked" for r in reels)
    nn = sum(1 for r in reels if r["mode"] != "blocked" and not r["views"])
    today = dt.date.today().strftime("%d.%m.%Y")
    n_total, n_sc = len(reels), len(scored)
    if n_sc:
        lead = L(f"{n_total} reeli: {n_sc} z liczbą wyświetleń" + (f", {nb} tylko z opisem" if nb else "") + ". Co mówią i co z tego wynika.",
                 f"{n_total} reels: {n_sc} with view counts" + (f", {nb} caption only" if nb else "") + ". What they say and what follows.")
    else:
        lead = L(f"{n_total} reeli z pełnym tekstem. Liczby wyświetleń nie zostały jeszcze dodane.", f"{n_total} reels with full text. View counts have not been added yet.")
    foot = []
    if med:
        foot.append([L("Jak czytać liczby.", "How to read the numbers."),
                     L(f" „Typowy reel” to mediana wyświetleń reeli, dla których znamy liczbę wyświetleń ({compact(med)}). „39×” znaczy: 39 razy więcej niż typowy reel.",
                       f" “Typical reel” is the median views of reels with known view counts ({compact(med)}). “39×” means 39 times the typical reel.")])
    foot.append([L("Skąd tekst.", "Where the text comes from."),
                 L(" Mowa jest zamieniona na tekst automatycznie; pojedyncze niepewne słowa mogą różnić się od nagrania. Tekst z ekranu odczytano automatycznie.",
                   " Speech is converted to text automatically; single uncertain words may differ from the recording. On-screen text was read automatically.")])
    miss = (L(f" {nb} reeli ma tylko opis i liczby (Instagram nie pozwala ich pobrać bez zalogowania).", f" {nb} reels have only caption and numbers (Instagram does not allow downloading them without a login).") if nb else "") + \
           (L(f" {nn} reeli nie ma znanej liczby wyświetleń.", f" {nn} reels have no known view count.") if nn else "")
    if miss:
        foot.append([L("Czego brakuje.", "What is missing."), miss])
    payload = {"lang": LANG, "ui": ({**UI[LANG], "h_topics": UI[LANG]["h_topics_auto"], "topics_intro": UI[LANG]["topics_intro_auto"]} if auto else UI[LANG]), "profile": handle, "median": med, "n_scored": n_sc, "n_total": n_total,
               "kicker": L(f"Profil @{handle} · stan na {today}", f"Profile @{handle} · as of {today}"), "title": L(f"Reele @{handle}", f"Reels @{handle}"),
               "lead": lead, "foot": foot, "findings": finds, "chart": chart, "advice": advice,
               "features": [{**r, "med_yes": compact(r.get("med_yes")), "med_no": compact(r.get("med_no")), "ratio_s": (f"{times(r['ratio'])} ({times(r['lo'])} - {times(r['hi'])})" if r["ratio"] else "-")}
                            for r in feature_table(scored, LANG)] if len(scored) >= 10 else [],
               "reels": sorted(reels, key=lambda r: -(r["views"] or 0))}
    tpl = (CODE / "page_template.html").read_text(encoding="utf-8")
    frag = tpl.replace("{{TITLE}}", payload["title"]).replace("{{LANG}}", LANG).replace("/*DATA*/null", json.dumps(payload, ensure_ascii=False))
    (WORK / "page.fragment.html").write_text(frag, encoding="utf-8")
    (WORK / "Reele.html").write_text(
        f'<!doctype html><html lang="{LANG}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">' + ICON_LINK + "</head><body>" + frag + "</body></html>", encoding="utf-8")
    print(f"Reele.html ({LANG}): {len(reels)} reels ({n_sc} with views), profile @{handle}, median {compact(med) if med else '-'}")


if __name__ == "__main__":
    main()
