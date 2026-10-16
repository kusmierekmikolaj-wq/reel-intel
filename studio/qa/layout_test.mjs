// layout_test.mjs - tests the pure layout functions of src/layout.ts in plain node (no browser, no render).
//   cd studio && node qa/layout_test.mjs        exit code 0 = all pass
// layout.ts is compiled on the fly with esbuild (already installed with Remotion).
import { readFileSync } from "node:fs";
import { transformSync } from "esbuild";

const src = readFileSync(new URL("../src/layout.ts", import.meta.url), "utf8");
const js = transformSync(src, { loader: "ts", format: "esm", target: "es2020" }).code;
const L = await import("data:text/javascript;base64," + Buffer.from(js).toString("base64"));

let pass = 0;
const failures = [];
const check = (cond, msg) => {
  if (cond) pass++;
  else failures.push(msg);
};

const SAFE = L.SAFE;
const BOTTOM = L.CANVAS.h - SAFE.bottom;                  // 1480

// ---- 1. computeLayout: every combination of 1-4 slogan lines, 1-5 caption lines, 0-2 rows of chips, with and without mascot / slogan
let combos = 0;
let sizes = {};
for (const hasSlogan of [true, false])
  for (const mascot of [true, false])
    for (let chipRows = 0; chipRows <= 2; chipRows++)
      for (let sl = 1; sl <= (hasSlogan ? 4 : 1); sl++)
        for (let cl = 1; cl <= 5; cl++) {
          const o = L.computeLayout(sl, cl, { hasSlogan, mascot, chipRows: hasSlogan ? chipRows : 0 });
          const tag = `[slogan ${hasSlogan ? sl : "pulse"}, caption ${cl}, chips ${chipRows}, mascot ${mascot}]`;
          combos++;
          check(o.ok, `${tag} does not fit: ${o.notes.join("; ")}`);
          check(o.slogan.font >= 96 && o.slogan.font <= 138, `${tag} slogan font ${o.slogan.font} out of 96..138`);
          check(o.caption.font >= 54 && o.caption.font <= 68, `${tag} caption font ${o.caption.font} out of 54..68`);
          const sloganBottom = o.slogan.top + o.slogan.height;
          const chipsBottom = o.chips ? o.chips.top + o.chips.height : sloganBottom;
          const cardBottom = o.caption.top + o.caption.height;
          check(o.slogan.top >= SAFE.top, `${tag} slogan top ${o.slogan.top} above the safe zone`);
          check(chipsBottom <= BOTTOM, `${tag} chips bottom ${chipsBottom} below the safe zone`);
          check(cardBottom <= BOTTOM, `${tag} card bottom ${cardBottom} below the safe zone`);
          check(cardBottom === o.bottom, `${tag} bottom field`);
          check(o.chips === null || o.chips.top === sloganBottom, `${tag} chips do not follow the slogan`);
          check(chipsBottom + L.GAP.caption <= o.caption.top, `${tag} slogan group (bottom ${chipsBottom}) is closer than ${L.GAP.caption} px to the card (top ${o.caption.top})`);
          check(o.caption.width <= L.CANVAS.w - 2 * SAFE.side, `${tag} card wider than the text column`);
          check(o.caption.top >= SAFE.top, `${tag} card above the safe zone`);
          if (o.mascot) {
            check(o.mascot.size >= L.MASCOT.min && o.mascot.size <= L.MASCOT.max, `${tag} mascot size ${o.mascot.size}`);
            check(o.mascot.headTop >= SAFE.top, `${tag} mascot head ${o.mascot.headTop} above the safe zone`);
            check(o.mascot.bottom + L.GAP.mascot <= o.slogan.top, `${tag} mascot bottom ${o.mascot.bottom} closer than ${L.GAP.mascot} px to the slogan (top ${o.slogan.top})`);
            check(o.mascot.top >= o.progress.y + o.progress.h + 12, `${tag} mascot box (top ${o.mascot.top}) touches the progress bar (y ${o.progress.y})`);
          }
          check(o.progress.y + o.progress.h <= SAFE.top, `${tag} progress bar is not in the free margin above the safe zone`);
          check(o.progress.x >= SAFE.side && o.progress.x + o.progress.w <= L.CANVAS.w - SAFE.side, `${tag} progress bar outside the side margins`);
          if (mascot && hasSlogan && chipRows === 0) sizes[`${sl}/${cl}`] = `${o.mascot ? o.mascot.size : 0}@${o.slogan.font}/${o.caption.font}`;
          if (mascot && hasSlogan && chipRows === 0 && sl <= 2 && cl <= 2) check(o.mascot && o.mascot.size >= L.MASCOT.pref, `${tag} a light scene must keep a comfortable mascot`);
        }
check(combos === 2 * 3 * 4 * 5 + 2 * 3 * 1 * 5, `expected ${2 * 3 * 4 * 5 + 2 * 3 * 5} layout combinations, ran ${combos}`);

// monotone: more lines never give a bigger font (the mascot may grow again when the fonts step down to make room for it)
for (const mascot of [true, false])
  for (let chipRows = 0; chipRows <= 2; chipRows++) {
    for (let cl = 1; cl <= 5; cl++)
      for (let sl = 1; sl < 4; sl++) {
        const a = L.computeLayout(sl, cl, { chipRows, mascot });
        const b = L.computeLayout(sl + 1, cl, { chipRows, mascot });
        check(b.slogan.font <= a.slogan.font && b.caption.font <= a.caption.font, `more slogan lines (${sl}->${sl + 1}, caption ${cl}) gave bigger fonts`);
      }
    for (let sl = 1; sl <= 4; sl++)
      for (let cl = 1; cl < 5; cl++) {
        const a = L.computeLayout(sl, cl, { chipRows, mascot });
        const b = L.computeLayout(sl, cl + 1, { chipRows, mascot });
        check(b.slogan.font <= a.slogan.font && b.caption.font <= a.caption.font, `more caption lines (${cl}->${cl + 1}, slogan ${sl}) gave bigger fonts`);
      }
  }

// the worst case: 4 slogan lines, 5 caption lines, chips in two rows: the fonts reach their floor and the mascot goes, nothing else
const worst = L.computeLayout(4, 5, { chipRows: 2 });
check(worst.slogan.font === 96 && worst.caption.font === 54, `worst case fonts ${worst.slogan.font}/${worst.caption.font}`);
check(worst.ok && worst.chips !== null && worst.mascot === null, "worst case: text and chips fit, the mascot is hidden");
// three rows of chips cannot fit next to 4 + 5 lines: the chips go, and it says so
const over = L.computeLayout(4, 5, { chipRows: 3 });
check(over.ok && over.chips === null && over.notes.some((n) => n.includes("chips")), "four slogan lines + five caption lines + 3 chip rows: chips dropped with a note");
// a different profile moves everything
const wide = L.computeLayout(3, 3, { safe: { top: 200, bottom: 350, side: 65 } });
check(wide.slogan.top >= 200 && wide.bottom <= 1920 - 350 && wide.textW === 1080 - 130 && wide.progress.y === 80, "a profile with top 200 / bottom 350 / side 65 is followed");
const tight = L.computeLayout(3, 3, { safe: { top: 270, bottom: 672, side: 130 } });
check(tight.ok && tight.bottom <= 1920 - 672 && tight.caption.width === 1080 - 260, "the most careful source (bottom 672, side 130) still fits 3 + 3 lines");

// ---- 2. wrapping and fitScene
check(JSON.stringify(L.wrapLines([100, 100, 100], 10, 215)) === "[[0,1],[2]]", "wrapLines greedy");
check(JSON.stringify(L.wrapLines([300, 50], 10, 200)) === "[[0],[1]]", "wrapLines: a word wider than the line stands alone");
check(L.wrapLines([], 10, 100).length === 0, "wrapLines empty");
const scenes = [
  { screen: "Free tool", words: "Try it today.".split(" ") },
  { screen: "QUALITY CONTROL REPORT", words: "You made thirty reels this month and honestly you watched none of them, not even your best ones, so who were they for?".split(" ") },
  { screen: "Misunderstanding Communication Infrastructure Transformation Implementation", words: "You made thirty reels this month and honestly you watched none of them, not even your best ones, so who were they for?".split(" "), chips: ["/human", "SKILL.md", "ffprobe", "remotion"] },
  { screen: "", words: "Follow for the next one and tell me which of the thirty you finally watched to the end.".split(" ") },
];
for (const s of scenes)
  for (const mascot of [true, false]) {
    const fit = L.fitScene(s, L.estimateMeasure, { mascot });
    const o = fit.layout;
    const tag = `[fitScene "${s.screen.slice(0, 18)}" mascot ${mascot}]`;
    check(o.ok, `${tag} does not fit: ${o.notes.join("; ")}`);
    check(fit.slogan.length === (s.screen ? o.slogan.lines : 0), `${tag} slogan lines drawn ${fit.slogan.length} != counted ${o.slogan.lines}`);
    check(fit.caption.length === o.caption.lines, `${tag} caption lines drawn ${fit.caption.length} != counted ${o.caption.lines}`);
    check(fit.caption.flat().length === s.words.length && fit.caption.flat().every((v, i) => v === i), `${tag} the caption lines keep every word in order`);
    check(fit.slogan.flat().join(" ") === s.screen.split(/\s+/).filter(Boolean).join(" "), `${tag} the slogan lines keep every word in order`);
    const cardBottom = o.caption.top + o.caption.height;
    check(cardBottom <= BOTTOM, `${tag} card bottom ${cardBottom}`);
    check(o.slogan.top >= SAFE.top, `${tag} slogan top ${o.slogan.top}`);
  }

// ---- 3. scene windows: never overlapping, the fade completes before the next scene starts
const fps = 30;
const timelines = {
  "the generator (gap 0.28 s)": [[0.4, 2.6], [2.88, 5.2], [5.48, 8.0], [8.28, 11.0]],
  "no gap": [[0.4, 2.6], [2.6, 5.0], [5.0, 8.0]],
  "a tiny gap": [[0.4, 2.0], [2.1, 3.0], [3.1, 4.0]],
  "a long gap": [[0.4, 2.0], [4.0, 6.0]],
  "one scene": [[0.4, 3.0]],
  "very short scenes": [[0.2, 0.3], [0.5, 0.6], [0.8, 0.9]],
};
for (const [name, tl] of Object.entries(timelines)) {
  const sc = tl.map(([start, end]) => ({ start, end }));
  const total = Math.ceil((tl[tl.length - 1][1] + 0.6) * fps);
  const w = L.sceneWindows(sc, fps, total);
  check(w.length === sc.length, `${name}: ${w.length} windows for ${sc.length} scenes`);
  for (let k = 0; k < w.length; k++) {
    check(w[k].dur >= 1 && w[k].from >= 0, `${name}: window ${k} ${JSON.stringify(w[k])}`);
    if (k > 0) check(w[k - 1].from + w[k - 1].dur <= w[k].from, `${name}: scene ${k - 1} (until ${w[k - 1].from + w[k - 1].dur}) overlaps scene ${k} (from ${w[k].from})`);
    if (k < w.length - 1) {
      const outEnd = w[k].from + w[k].dur;
      check(w[k].fadeFrom < outEnd && outEnd - w[k].fadeFrom <= L.FADE_FRAMES, `${name}: fade of scene ${k} is ${outEnd - w[k].fadeFrom} frames`);
      const onset = Math.floor(sc[k + 1].start * fps + 1e-6);
      check(w[k + 1].from <= Math.max(onset, outEnd), `${name}: scene ${k + 1} starts at ${w[k + 1].from}, after its first word (${onset})`);
    } else {
      check(w[k].from + w[k].dur === total, `${name}: the last scene runs to the end`);
    }
  }
  if (name.startsWith("the generator")) {
    check(w[0].from === 9 && w[0].fadeFrom === 78 && w[0].from + w[0].dur === 84 && w[1].from === 84, `the generator timeline: window 0 ${JSON.stringify(w[0])}, scene 1 from ${w[1].from}`);
    for (let k = 1; k < w.length; k++) check(w[k].from <= Math.floor((tl[k][0] + 1e-6) * fps), `the generator timeline: scene ${k} appears before its first word`);
  }
}

// ---- 4. the mascot's size: constant inside a scene, never bigger than either scene's size around a change
{
  const sc = [{ start: 0.4, end: 2.6 }, { start: 2.88, end: 5.2 }, { start: 5.48, end: 8.0 }];
  const w = L.sceneWindows(sc, fps, 270);
  const s = [420, 300, 0];
  for (let f = 0; f < 270; f++) {
    const v = L.mascotSizeAt(f, w, s);
    check(v >= 0 && v <= 420 + 1e-9, `mascot size ${v} at frame ${f}`);
    const k = Math.max(0, w.findIndex((x, i) => f >= x.from && (i === w.length - 1 || f < w[i + 1].from)));
    check(v <= s[k] + 1e-9, `mascot size ${v} at frame ${f} is bigger than its scene's ${s[k]}`);
    // while a scene's text is on the picture (its window), the mascot is no bigger than that scene's slot
    if (f >= w[0].from && f < w[0].from + w[0].dur) check(v <= s[0] + 1e-9, `scene 0 frame ${f}`);
    if (f >= w[1].from && f < w[1].from + w[1].dur) check(v <= s[1] + 1e-9, `scene 1 frame ${f}: mascot ${v} is bigger than the slot ${s[1]}`);
  }
  check(L.mascotSizeAt(w[0].from + 40, w, s) === 420 && L.mascotSizeAt(w[1].from + 40, w, s) === 300, "mascot size is steady inside a scene");
  const lastOfScene0 = w[0].from + w[0].dur - 1;
  check(Math.abs(L.mascotSizeAt(lastOfScene0, w, s) - 300) < 1e-6, `the mascot has shrunk to the smaller slot on the last frame of the outgoing scene (${L.mascotSizeAt(lastOfScene0, w, s)})`);
  check(Math.abs(L.mascotSizeAt(w[1].from, w, s) - 300) < 1e-6, "and it starts the next scene from there");
}

console.log(`layout_test: ${pass} checks passed, ${failures.length} failed (${combos} layout combinations)`);
for (const f of failures.slice(0, 40)) console.log("  FAIL", f);
if (failures.length > 40) console.log(`  ... and ${failures.length - 40} more`);
const rows = [];
for (let sl = 1; sl <= 4; sl++) rows.push(`   slogan ${sl} lines: ` + [1, 2, 3, 4, 5].map((cl) => String(sizes[`${sl}/${cl}`]).padStart(11)).join(" "));
console.log("mascot size @ slogan font / caption font, without chips, caption lines 1..5 across:\n" + rows.join("\n"));
process.exit(failures.length ? 1 : 0);
