---
name: why-it-went-viral
description: Explain why a reel outperformed the rest of a profile and how to make a similar one. Use after scan-reels when the user asks why a reel exploded, what works on a profile, or wants a script modelled on the best reels.
---

# why-it-went-viral

Input: scanned reels (`scan-reels` done, `qa` without ERRORs). Output: `reel-data/data/<id>/analysis.md` for the outlier and, with 10+ reels, `reel-data/patterns.md`.

## Method (what makes the conclusions trustworthy)
1. **Define "exploded" relative to the profile:** views / median views of the scanned reels (`python "${CLAUDE_PLUGIN_ROOT}/scripts/reel.py" report` prints the median; >5x is an outlier).
2. **Compare numbers:** `python "$R" report compare <id>` gives the reel vs the others (length, speech rate, hook words, likes/views, comments/views, rank). Check **form** first: if tempo, hook speed and length match weaker reels, form does not explain the gap.
3. **Group by topic.** Label every reel and write `reel-data/topics.csv` (`id,group`; e.g. broad-audience tool tips, niche education, offer/testimonial, memes/other). Then `python "$R" report groups` prints group medians **and repeats them without the top reel and without the top 3**: a conclusion must survive that. In the reference run (33 reels) broad "tool everyone uses" reels had 7.8x the overall median (7.1x without the top reel, 5.4x without the top 3), niche reels 0.6-0.7x.
3b. **Rule out the cheap explanations with numbers, not feelings:** `python "$R" report timing` (posting hour / weekday vs views, permutation p-value) and `python "$R" report hooks` (hook type vs views; no conclusion where n < 5). In the reference run neither explained reach (p=0.87 / n<5).
4. **Separate CTA from reach.** Comments per view show how well a keyword CTA collects leads, not why reach grew. Check whether big reels without a keyword CTA exist.
5. **State hypotheses, not facts.** Every hypothesis gets a confidence (high/medium/low), the data that supports it and what would refute it. Retention, saves, shares and traffic sources are not public: say so.
6. **Re-check numbers when the data changes** (logged-in view counts differ from logged-out ones). Recompute medians and multipliers together, never mix baselines.

## analysis.md sections
1. Data: table vs the rest (+ topic-group table). 2. Hypotheses ranked, with confidence. 3. Structure of the reel by time (hook, steps, CTA, loop). 4. "How to make a similar one": a pattern to copy, never the content itself. 5. What is missing to raise confidence.

## Creating similar content
Use the pattern, not the wording: topic breadth, hook formula, step structure, CTA mechanic. Write the new script in the user's own niche and voice. If the Social Media Skills plugin is installed, `hook-writer`, `reels-script` and `viral-reverse-engineering` help with the writing step.
Do not republish another creator's recordings or text 1:1.
