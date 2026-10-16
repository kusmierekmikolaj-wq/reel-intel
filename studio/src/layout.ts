/**
 * layout.ts - where everything stands on the 1080x1920 picture. Pure functions of numbers: no React, no DOM, so they are tested in plain node
 * (qa/layout_test.mjs) and the same numbers can be read by the Python quality checker.
 *
 * The rule: nothing that is read (slogan, chips, caption) leaves the safe zone, and nothing overlaps another thing, for 1-4 slogan lines and
 * 1-5 caption lines. The safe zone is the profile of qa/profile.py (safe_zone: top 270, bottom 440, side 90; the sources disagree about the
 * platform's own buttons and caption, so the numbers can be passed in as the `safe` prop). Key content lives in y 270..1480, x 90..990.
 *
 * Order from the top: mascot, slogan (+ chips), caption card. When they do not all fit at full size, in this order:
 *   1. both fonts go down in steps (slogan from 138/118 px, never below 96; caption from 68 px, never below 54) while the mascot would be smaller
 *      than MASCOT.pref;
 *   2. the mascot shrinks (down to MASCOT.min), then disappears for that scene;
 *   3. only when slogan, chips and caption do not fit even then, the chips are dropped (and `notes` says so).
 * The progress bar is decoration: it sits at y 150 in the free margin above the safe zone, where the platform may cover it without a loss.
 */

export const CANVAS = { w: 1080, h: 1920 } as const;
export type Safe = { top: number; bottom: number; side: number };
export const SAFE: Safe = { top: 270, bottom: 440, side: 90 };

export const FONT_DISPLAY = '"Bahnschrift", "Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif';
export const FONT_MONO = '"Cascadia Mono", Consolas, monospace';

/** the design numbers. Sizes are px on the 1080x1920 frame; "per px" values scale with the font size. */
export const SLOGAN = { big: 138, long: 118, longChars: 22, min: 96, step: 0.93, lineH: 1.04, pad: 0.13, gap: 0.27, spacing: 0.03, weight: 700 };
export const CAPTION = { big: 68, min: 54, step: 0.93, lineH: 1.28, padV: 0.68, padH: 44, gap: 0.35, shell: 12, spacing: -1, weight: 600 };
export const CHIPS = { font: 48, lineH: 58, padV: 16, padH: 28, gap: 18, rowGap: 18, marginTop: 36, weight: 600 };
/** the mascot's drawing: a 660x560 view box whose head starts 100 units down; `size` is the drawing's scale (520 = 1 unit per px) */
export const MASCOT = { viewW: 660, viewH: 560, head: 100, ref: 520, max: 520, pref: 360, min: 230 };
export const PULSE = { size: 340, ref: 420 };
export const GAP = { mascot: 40, caption: 36, extra: 40 };           // minimum space under the mascot and above the card; the extra grows when there is room
export const SLACK = 12;                                             // px kept free on every line: the measured width may be a little off, the lit word grows 4 %
export const PROGRESS = { h: 8 };

const VIS = (MASCOT.viewH - MASCOT.head) / MASCOT.ref;               // visible part of the mascot (head top to the bottom of the body) per px of size

export type Opts = {
  safe?: Safe;
  hasSlogan?: boolean;          // false: the call to action scene shows a pulse instead of a slogan
  chipRows?: number;            // rows of chips under the slogan (0: none)
  mascot?: boolean;             // false: the reel has no mascot
  sloganMax?: number;           // largest slogan font (default: 138 px, 118 px for a long slogan)
  captionMax?: number;
};

export type Layout = {
  ok: boolean;                  // slogan, chips and caption fit in the safe zone
  comfortable: boolean;         // ok, and the mascot (if wanted) has at least MASCOT.pref
  notes: string[];
  safe: Safe;
  textW: number;                // width of the text column: the frame minus both side margins
  slogan: { font: number; lines: number; lineH: number; pad: number; pitch: number; gap: number; spacing: number; top: number; height: number };
  pulse: { size: number; top: number } | null;
  chips: { rows: number; top: number; height: number } | null;
  caption: { font: number; lines: number; lineH: number; padV: number; padH: number; gap: number; spacing: number; top: number; height: number; width: number; textW: number };
  mascot: { size: number; top: number; headTop: number; bottom: number } | null;
  progress: { x: number; y: number; w: number; h: number };
  bottom: number;               // the lowest pixel of the caption card
};

export const sloganMetrics = (font: number) => {
  const lineH = Math.round(font * SLOGAN.lineH);
  const pad = Math.round(font * SLOGAN.pad);
  return { font, lineH, pad, pitch: lineH + pad, gap: Math.round(font * SLOGAN.gap), spacing: -Math.round(font * SLOGAN.spacing) };
};

export const captionMetrics = (font: number, textW: number) => {
  const width = textW;
  return { font, lineH: Math.round(font * CAPTION.lineH), padV: Math.round(font * CAPTION.padV), padH: CAPTION.padH, gap: Math.round(font * CAPTION.gap),
           spacing: CAPTION.spacing, width, textW: width - 2 * CAPTION.shell - 2 * CAPTION.padH };
};

export const chipsHeight = (rows: number) => (rows > 0 ? CHIPS.marginTop + rows * (CHIPS.lineH + 2 * CHIPS.padV) + (rows - 1) * CHIPS.rowGap : 0);
export const mascotBox = (size: number) => ({ w: (size * MASCOT.viewW) / MASCOT.ref, h: (size * MASCOT.viewH) / MASCOT.ref, headY: (size * MASCOT.head) / MASCOT.ref, visible: size * VIS });
/** where the mascot's box starts so that its head top is at `headTop` */
export const mascotTop = (size: number, headTop: number) => headTop - mascotBox(size).headY;
export const progressBar = (safe: Safe = SAFE) => ({ x: safe.side, y: Math.max(40, safe.top - 120), w: CANVAS.w - 2 * safe.side, h: PROGRESS.h });

/** font sizes from `cap` down to `min`, each step 7 % smaller, `min` always last */
export function ladder(cap: number, min: number, step: number): number[] {
  const out = [cap];
  while (out[out.length - 1] > min) out.push(Math.max(min, Math.round(out[out.length - 1] * step)));
  return out;
}

/** the (slogan font, caption font) pairs tried in order, biggest first; both go down together so neither text is sacrificed for the other */
export function fontSteps(sloganMax: number = SLOGAN.big, captionMax: number = CAPTION.big): [number, number][] {
  const s = ladder(sloganMax, SLOGAN.min, SLOGAN.step);
  const c = ladder(captionMax, CAPTION.min, CAPTION.step);
  const n = Math.max(s.length, c.length);
  return Array.from({ length: n }, (_, k) => [s[Math.min(k, s.length - 1)], c[Math.min(k, c.length - 1)]] as [number, number]);
}

/** the layout for given fonts and line counts (no searching): positions of every block, and whether it all fits */
function place(sl: number, cl: number, sf: number, cf: number, o: Opts): Layout {
  const safe = o.safe ?? SAFE;
  const textW = CANVAS.w - 2 * safe.side;
  const headTop = safe.top + 10;                                      // the mascot's head starts 10 px inside the zone (it bobs by 9)
  const cardBottomMax = CANVAS.h - safe.bottom - 10;
  const avail = cardBottomMax - headTop;
  const sm = sloganMetrics(sf);
  const cm = captionMetrics(cf, textW);
  const hasSlogan = o.hasSlogan !== false;
  const rows = Math.max(0, o.chipRows ?? 0);
  const Hs = hasSlogan ? sl * sm.pitch : PULSE.size;
  const Hch = chipsHeight(rows);
  const Hg = Hs + Hch;
  const Hc = 2 * CAPTION.shell + 2 * cm.padV + cl * cm.lineH;
  const textNeed = Hg + GAP.caption + Hc;
  const ok = textNeed <= avail;
  let size = 0;
  if (o.mascot !== false) {
    const s = Math.min(MASCOT.max, Math.floor((avail - GAP.mascot - textNeed) / VIS));
    if (s >= MASCOT.min) size = s;
  }
  const notes: string[] = [];
  let sloganTop: number;
  let cardTop: number;
  let mascot: Layout["mascot"] = null;
  if (size > 0) {
    const visible = size * VIS;
    const extra = Math.min(GAP.extra, Math.max(0, (avail - (visible + GAP.mascot + textNeed)) / 2));
    sloganTop = headTop + visible + GAP.mascot + extra;
    cardTop = sloganTop + Hg + GAP.caption + extra;
    mascot = { size, top: Math.round(mascotTop(size, headTop)), headTop, bottom: Math.round(headTop + visible) };
  } else {
    const g2 = Math.max(GAP.caption, Math.min(100, avail - Hg - Hc));
    const top = headTop + Math.max(0, (avail - (Hg + g2 + Hc)) / 2);
    sloganTop = top;
    cardTop = top + Hg + g2;
    if (o.mascot !== false) notes.push("the mascot is hidden in this scene: no room for it next to the text");
  }
  sloganTop = Math.round(sloganTop);
  cardTop = Math.round(cardTop);
  if (!ok) notes.push(`the text does not fit the safe zone (needs ${Math.round(textNeed)} px of ${avail})`);
  return {
    ok, comfortable: ok && (o.mascot === false || size >= MASCOT.pref), notes, safe, textW,
    slogan: { font: sf, lines: sl, lineH: sm.lineH, pad: sm.pad, pitch: sm.pitch, gap: sm.gap, spacing: sm.spacing, top: sloganTop, height: Hs },
    pulse: hasSlogan ? null : { size: PULSE.size, top: sloganTop },
    chips: rows > 0 ? { rows, top: sloganTop + Hs, height: Hch } : null,
    caption: { font: cf, lines: cl, lineH: cm.lineH, padV: cm.padV, padH: cm.padH, gap: cm.gap, spacing: cm.spacing, top: cardTop, height: Hc, width: cm.width, textW: cm.textW },
    mascot,
    progress: progressBar(safe),
    bottom: cardTop + Hc,
  };
}

export type Counts = { slogan: number; caption: number; wide?: boolean };

/** the biggest fonts for which everything fits comfortably. `countsAt(sloganFont, captionFont)` says how many lines each text has at those fonts
 *  (a smaller font wraps into fewer lines); `wide` marks a font at which a single word is wider than the column. */
export function solve(countsAt: (sloganFont: number, captionFont: number) => Counts, o: Opts = {}): Layout {
  const steps = fontSteps(o.sloganMax ?? SLOGAN.big, o.captionMax ?? CAPTION.big);
  let best: Layout | null = null;
  for (const [sf, cf] of steps) {
    const c = countsAt(sf, cf);
    best = place(c.slogan, c.caption, sf, cf, o);
    if (best.comfortable && !c.wide) return best;
  }
  if (best && !best.ok && (o.chipRows ?? 0) > 0) {                    // even the smallest fonts do not leave room for the chips: they go
    const l = solve(countsAt, { ...o, chipRows: 0 });
    l.notes.push("the chips were dropped: no room for them next to the slogan and the caption");
    return l;
  }
  return best as Layout;
}

/** THE layout function: from the number of slogan lines and caption lines (and the options) to every position and font size */
export function computeLayout(sloganLines: number, captionLines: number, o: Opts = {}): Layout {
  return solve(() => ({ slogan: sloganLines, caption: captionLines }), o);
}

// ---------------------------------------------------------------- text: measuring and wrapping into explicit lines

export type FontSpec = { family: "display" | "mono"; weight: number; size: number; letterSpacing: number };
export type Measure = (text: string, font: FontSpec) => number;

/** a rough width without a browser (node tests, server side): good to about 8 % */
export const estimateMeasure: Measure = (text, f) => text.length * f.size * (f.family === "mono" ? 0.6 : f.weight >= 700 ? 0.49 : 0.47) + f.letterSpacing * text.length;

let ctx: CanvasRenderingContext2D | null | undefined;
/** the width the browser really gives a text: the same font string as the CSS, measured on a canvas */
export const browserMeasure: Measure = (text, f) => {
  if (ctx === undefined) {
    try {
      ctx = typeof document !== "undefined" ? document.createElement("canvas").getContext("2d") : null;
    } catch {
      ctx = null;
    }
  }
  if (!ctx) return estimateMeasure(text, f);
  ctx.font = `${f.weight} ${f.size}px ${f.family === "mono" ? FONT_MONO : FONT_DISPLAY}`;
  if ("letterSpacing" in ctx) {
    (ctx as any).letterSpacing = `${f.letterSpacing}px`;
    return ctx.measureText(text).width;
  }
  return ctx.measureText(text).width + f.letterSpacing * text.length;
};

/** greedy wrapping: indexes of the words on each line; a word wider than the line stands alone */
export function wrapLines(widths: number[], gap: number, maxW: number): number[][] {
  const lines: number[][] = [];
  let cur: number[] = [];
  let w = 0;
  widths.forEach((ww, i) => {
    const nw = cur.length ? w + gap + ww : ww;
    if (cur.length && nw > maxW) {
      lines.push(cur);
      cur = [i];
      w = ww;
    } else {
      cur.push(i);
      w = nw;
    }
  });
  if (cur.length) lines.push(cur);
  return lines;
}

export type SceneText = { screen: string; chips?: string[]; words: string[] };
export type Fitted = { layout: Layout; slogan: string[][]; chips: string[][]; caption: number[][] };

/** wraps a scene's texts into lines and finds the fonts and positions: the slogan and caption are drawn exactly as these lines, so what the layout
 *  counted is what appears */
export function fitScene(scene: SceneText, measure: Measure, o: { mascot?: boolean; safe?: Safe } = {}): Fitted {
  const safe = o.safe ?? SAFE;
  const textW = CANVAS.w - 2 * safe.side;
  const sWords = scene.screen.split(/\s+/).filter(Boolean);
  const chips = (scene.chips ?? []).filter(Boolean);
  const sloganMax = sWords.join(" ").length > SLOGAN.longChars ? SLOGAN.long : SLOGAN.big;
  const cm = (font: number) => captionMetrics(font, textW);
  const chipW = chips.map((c) => measure(c, { family: "mono", weight: CHIPS.weight, size: CHIPS.font, letterSpacing: 0 }) + 2 * CHIPS.padH);
  const chipRows = wrapLines(chipW, CHIPS.gap, textW - SLACK);
  const wrapSlogan = (font: number) => {
    const m = sloganMetrics(font);
    const widths = sWords.map((w) => measure(w, { family: "display", weight: SLOGAN.weight, size: font, letterSpacing: m.spacing }));
    return { lines: wrapLines(widths, m.gap, textW - SLACK), wide: widths.some((x) => x > textW - SLACK) };
  };
  const wrapCaption = (font: number) => {
    const m = cm(font);
    const widths = scene.words.map((w) => measure(w, { family: "display", weight: CAPTION.weight, size: font, letterSpacing: m.spacing }));
    return { lines: wrapLines(widths, m.gap, m.textW - SLACK), wide: widths.some((x) => x > m.textW - SLACK) };
  };
  const layout = solve((sf, cf) => {
    const s = wrapSlogan(sf);
    const c = wrapCaption(cf);
    return { slogan: Math.max(sWords.length ? 1 : 0, s.lines.length), caption: Math.max(1, c.lines.length), wide: s.wide || c.wide };
  }, { safe, hasSlogan: sWords.length > 0, chipRows: chipRows.length, mascot: o.mascot, sloganMax });
  return {
    layout,
    slogan: wrapSlogan(layout.slogan.font).lines.map((ln) => ln.map((i) => sWords[i])),
    chips: layout.chips ? chipRows.map((ln) => ln.map((i) => chips[i])) : [],
    caption: wrapCaption(layout.caption.font).lines,
  };
}

// ---------------------------------------------------------------- time: when each scene is on the picture

export const FADE_FRAMES = 6;       // the outgoing scene fades out in this many frames
export const LEAD_SECONDS = 0.1;    // a scene appears this long before its first word
export const GROW_FRAMES = 10;      // the mascot grows back to its size in this many frames after a scene change

export type Win = { from: number; dur: number; fadeFrom: number; last: boolean };

const fr = (seconds: number, fps: number) => Math.round(seconds * fps * 1e6) / 1e6;      // 2.6 s * 30 must be frame 78, not 78.00000000000001

/** the frames in which each scene is shown. The windows never overlap: the outgoing scene fades out completely (FADE_FRAMES) before the
 *  incoming one starts, so two scenes' texts are never on the picture together. The background and the mascot belong to no scene: they go on. */
export function sceneWindows(scenes: { start: number; end: number }[], fps: number, totalFrames: number): Win[] {
  const lead = Math.round(LEAD_SECONDS * fps);
  const out: Win[] = [];
  let from = scenes.length ? Math.max(0, Math.floor(fr(scenes[0].start, fps)) - lead) : 0;
  for (let k = 0; k < scenes.length; k++) {
    if (k === scenes.length - 1) {
      out.push({ from, dur: Math.max(1, totalFrames - from), fadeFrom: Number.POSITIVE_INFINITY, last: true });
      break;
    }
    const onset = Math.floor(fr(scenes[k + 1].start, fps));                          // the frame of the next scene's first word
    const fadeEnd = Math.ceil(fr(scenes[k].end, fps)) + FADE_FRAMES;                  // the voice is over, then the fade
    const next = Math.max(from + 1, onset - lead, Math.min(fadeEnd, onset));         // the incoming scene's first frame
    const outEnd = Math.max(from + 1, Math.min(next, fadeEnd));
    out.push({ from, dur: outEnd - from, fadeFrom: Math.max(from, outEnd - FADE_FRAMES), last: false });
    from = next;
  }
  return out;
}

const ease = (t: number) => t * t * (3 - 2 * t);
const clamp01 = (x: number) => Math.max(0, Math.min(1, x));

/** the mascot's size at a frame: constant inside a scene; at a scene change it shrinks to the smaller of the two scenes' sizes while the old
 *  text fades, and grows to the new size after the new text has appeared, so it never covers either scene's text */
export function mascotSizeAt(frame: number, wins: Win[], sizes: number[]): number {
  if (!wins.length) return 0;
  let k = 0;
  for (let i = 0; i < wins.length; i++) if (frame >= wins[i].from) k = i;
  const size = sizes[k];
  let grow = size;
  if (k > 0) {
    const from = Math.min(sizes[k - 1], size);
    grow = from + (size - from) * ease(clamp01((frame - wins[k].from) / GROW_FRAMES));
  }
  let shrink = size;
  if (k < wins.length - 1) {
    const to = Math.min(size, sizes[k + 1]);
    shrink = size + (to - size) * ease(clamp01((frame - wins[k].fadeFrom + 1) / FADE_FRAMES));
  }
  return Math.min(grow, shrink);
}
