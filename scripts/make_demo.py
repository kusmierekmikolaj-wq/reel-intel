"""make_demo.py - regenerate src/reel_intel/demo/ : a SYNTHETIC profile ("demo.coffee", 16 reels about home coffee).
No content from any platform, no real person: invented texts and numbers that look like a real scan, so `reel-intel demo`
works offline and everyone can try the page in a minute.   python scripts/make_demo.py
"""
import datetime as dt
import json
import shutil
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "reel_intel" / "demo"
TIPS, GEAR, BTS, PROMO = "Quick tips for everyone", "Gear and reviews", "Behind the scenes", "Offers and promos"

# id, date, hour, seconds, views, likes, comments, group, about, caption, speech sentences (None = no speech), text on screen
R = [
    ("D01", "2026-09-28", 18, 32, 412000, 9800, 310, TIPS, "One change that makes cheap coffee taste like a cafe: use water at 93 degrees, not boiling.",
     "Stop pouring boiling water on your coffee.", ["Stop pouring boiling water on your coffee.", "Boiling water burns the grounds and makes the cup bitter.", "Wait thirty seconds after the kettle clicks.", "Then pour slowly in circles.", "Same beans, completely different cup. Save this for tomorrow morning."], None),
    ("D02", "2026-09-14", 17, 28, 187000, 5100, 140, TIPS, "Three-second trick to stop your milk from splitting in hot coffee.",
     "Why does my milk curdle?", ["Why does your milk curdle in hot coffee?", "Because the coffee is acidic and the milk is cold.", "Warm the milk for ten seconds first.", "Now it blends smoothly. Send this to someone who still drinks it black out of fear."], None),
    ("D03", "2026-08-30", 19, 41, 96000, 2300, 88, TIPS, "How to grind without a grinder: the rolling pin method and why it is not as bad as it sounds.",
     "No grinder? No problem.", ["No grinder, no problem.", "Put the beans in a bag, roll over them with a rolling pin.", "Aim for the size of coarse sand.", "It will not be perfect, but it beats stale pre-ground coffee every time."], None),
    ("D04", "2026-08-12", 12, 24, 61000, 1500, 51, TIPS, "Store your beans in the dark, never in the fridge.",
     "Your fridge is ruining your beans.", ["Your fridge is ruining your beans.", "Moisture and smells get into the beans.", "Keep them in an airtight container in a dark cupboard.", "That is it. Follow for more small fixes."], None),
    ("D05", "2026-07-22", 18, 9, 38500, 1200, 20, TIPS, "Text-only reel: the 4-minute pour-over timeline.",
     "The 4-minute pour-over, step by step", None, "Bloom 30 s / Pour to 150 g / Pour to 250 g / Done at 3:30"),
    ("D06", "2026-09-05", 16, 58, 33000, 760, 44, GEAR, "Honest review of a 25 euro hand grinder after six months of daily use.",
     "Six months with a cheap hand grinder.", ["I used this twenty five euro hand grinder every day for six months.", "The burrs still cut evenly, which surprised me.", "The handle wobbles and the lid is loose.", "For the price it is the best upgrade you can make. Link in bio if you want the model."], None),
    ("D07", "2026-07-02", 20, 47, 27500, 540, 29, GEAR, "Is a 300 euro kettle worth it? Temperature control compared with a basic kettle.",
     "Is a 300 euro kettle worth it?", ["Is a three hundred euro kettle worth it?", "I tested it against a basic kettle with a thermometer.", "The cheap one was off by eight degrees. The expensive one by one.", "That difference is audible in the cup, but only for light roasts."], None),
    ("D08", "2026-06-18", 17, 52, 24800, 480, 33, GEAR, "French press vs aeropress: five blind tastings with friends.",
     "Blind taste test: press vs aeropress", ["We did five blind tastings with three friends.", "Aeropress won four times for clarity.", "French press won once, with a dark roast.", "So it depends on the beans, not on the gadget."], None),
    ("D09", "2026-06-02", 21, 63, 14200, 410, 12, BTS, "A day in the life of a one-person roastery.",
     "A day at the roastery", ["It is six in the morning and the roaster is already warm.", "Today I am roasting three small batches.", "Most of the job is cleaning and packing, not the pretty part.", "If you are curious how it works, ask me anything in the comments."], None),
    ("D10", "2026-05-20", 19, 35, 11900, 300, 17, BTS, "Why this roastery moved to a smaller space.",
     "We moved. Here is why.", ["We moved the roastery to a smaller space last month.", "Rent was eating the profit, and nobody could tell the difference in the coffee.", "Smaller room, same beans, better margins."], None),
    ("D11", "2026-05-06", 14, 12, 9400, 210, 9, BTS, "Time-lapse of a packing day.",
     "Packing day time-lapse", None, "400 bags / 6 hours / 1 very sore wrist"),
    ("D12", "2026-04-22", 18, 44, 8700, 190, 61, PROMO, "Launch of the monthly subscription: three beans, free shipping.",
     "New: monthly coffee subscription", ["The monthly subscription is open.", "You get three different beans every month with free shipping.", "Comment the word BEANS and I will send you the first month discount."], None),
    ("D13", "2026-04-08", 20, 38, 7600, 150, 54, PROMO, "Black Friday preview: 20 percent off the starter kit.",
     "Starter kit sale", ["The starter kit is twenty percent off this week.", "It includes a grinder, a pour-over and a pound of beans.", "Comment KIT and I will send you the link."], None),
    ("D14", "2026-03-25", 17, 55, 6900, 120, 30, PROMO, "Customer story: how Anna improved her morning coffee in a week.",
     "Anna's one-week coffee glow-up", ["Anna started with instant coffee and a dull kettle.", "In one week she switched to fresh beans and a scale.", "She says mornings finally feel like a ritual. Comment RITUAL for her exact setup."], None),
    ("D15", "2026-03-11", 13, 21, 5200, 90, 6, BTS, "Out-takes from filming the grinder review.",
     "Bloopers", None, "Take 14 / the lid fell off again / we are still friends"),
    ("D16", "2026-02-24", 22, 70, 4300, 70, 18, PROMO, "Reminder that the subscription closes on Sunday.",
     "Last call", ["Last call for the subscription before the price changes on Sunday.", "If you were waiting for a sign, this is it.", "Comment BEANS to get the link."], None),
]


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "data").mkdir(parents=True)
    topics = ["id,group"]
    for rid, date, hour, sec, views, likes, comments, group, about, caption, sentences, screen in R:
        d = OUT / "data" / rid
        d.mkdir()
        day = dt.datetime.fromisoformat(date).replace(hour=hour)
        segs, t = [], 0.0
        for sent in (sentences or []):
            n = len(sent.split())
            dur = n * 0.38
            words = [{"w": w, "s": round(t + i * dur / n, 2), "e": round(t + (i + 1) * dur / n, 2), "p": 0.97} for i, w in enumerate(sent.split())]
            segs.append({"start": round(t, 2), "end": round(t + dur, 2), "text": sent, "words": words})
            t += dur + 0.5
        n_words = sum(len(s["words"]) for s in segs)
        feats = {"id": rid, "url": f"https://example.com/demo/{rid}", "uploader": "demo.coffee", "upload_date": day.strftime("%Y%m%d"),
                 "timestamp": int(day.timestamp()), "hour": hour, "weekday": day.weekday(), "duration_s": float(sec), "views": float(views),
                 "likes": likes, "comments": comments, "caption": caption, "hashtags": [], "language": "en", "word_count": n_words,
                 "words_per_min": round(n_words / (sec / 60), 1), "first_word_at_s": 0.0 if segs else None,
                 "hook_spoken": segs[0]["text"] if segs else "", "hook_words_3s": sum(1 for s in segs for w in s["words"] if w["s"] < 3),
                 "question_marks": sum(s["text"].count("?") for s in segs), "digits_in_speech": 0, "speech_reliable": bool(segs)}
        (d / "features.json").write_text(json.dumps(feats, ensure_ascii=False, indent=1), encoding="utf-8")
        (d / "transcript.json").write_text(json.dumps({"prompt_hash": "demo", "language": "en", "language_probability": 1.0, "duration": float(sec), "segments": segs},
                                                      ensure_ascii=False, indent=1), encoding="utf-8")
        (d / "meta.json").write_text(json.dumps({"id": rid, "description": caption, "uploader": "demo.coffee"}), encoding="utf-8")
        short = f"# {rid}\n\n**About:** {about}\n"
        if screen:
            short += f"**Content:** On-screen text: {screen}\n"
        (d / "content_short.md").write_text(short, encoding="utf-8")
        if screen:
            (d / "onscreen.json").write_text(json.dumps([{"t": 0.3, "text": [screen]}]), encoding="utf-8")
        topics.append(f"{rid},{group}")
    (OUT / "topics.csv").write_text("\n".join(topics) + "\n", encoding="utf-8")
    (OUT / "config.yaml").write_text("ui_language: en\n", encoding="utf-8")
    print(f"demo: {len(R)} synthetic reels -> {OUT}")


if __name__ == "__main__":
    main()
