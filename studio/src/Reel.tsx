import React, { useMemo } from "react";
import { Mascot } from "./Mascot";
import { AbsoluteFill, Audio, Easing, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { CAPTION, CHIPS, FONT_DISPLAY, FONT_MONO, PULSE, SAFE, Fitted, Layout, Safe, Win, browserMeasure, fitScene, mascotSizeAt, mascotTop, progressBar, sceneWindows } from "./layout";

export type Scene = {
  text: string;
  screen: string;
  emphasis: string[];
  chips?: string[];
  start: number;
  end: number;
  words: [string, number, number][];
};
export type ReelProps = {
  scenes: Scene[]; total: number; audio?: string; mouth?: number[]; vis?: number[]; mascot?: boolean;
  safe?: Safe;                  // the safe zone (qa/profile.py safe_zone): top, bottom, side in px; default top 270, bottom 440, side 90
  qaNoMascotArt?: boolean;      // QA only: keep the mascot's place but do not draw it (so a still can be compared with and without)
  qaTint?: boolean;             // QA only: every second scene's text is drawn green, so the two scenes of a transition can be told apart in a still
};

const NAVY = "#0A1733";
const PAPER = "#F2EEE3";
const SIGNAL = "#FFC24B";
const MUTE = "#9FB0D6";
const LINE = "#8FB4FF";
const clean = (w: string) => w.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
const TINT = "grayscale(1) sepia(1) hue-rotate(80deg) saturate(10) ";

/** film-grid background with a slow glow and a scan line: the motif of the product */
const Background: React.FC = () => {
  const f = useCurrentFrame();
  const { height } = useVideoConfig();
  const drift = (f * 0.5) % 120;
  const scanY = ((f * 3.1) % (height + 400)) - 200;
  return (
    <AbsoluteFill style={{ backgroundColor: NAVY }}>
      <AbsoluteFill style={{ background: `radial-gradient(900px 760px at ${78 + Math.sin(f / 90) * 8}% ${10 + Math.cos(f / 110) * 5}%, rgba(23,58,122,.95), transparent 70%), radial-gradient(800px 700px at ${12 + Math.cos(f / 130) * 6}% 92%, rgba(40,70,140,.55), transparent 70%)` }} />
      <AbsoluteFill style={{ backgroundImage: `repeating-linear-gradient(90deg, transparent 0 119px, rgba(143,180,255,.2) 119px 120px)`, backgroundPositionX: -drift, opacity: 0.8 }} />
      <AbsoluteFill style={{ backgroundImage: `repeating-linear-gradient(0deg, transparent 0 119px, rgba(143,180,255,.09) 119px 120px)`, backgroundPositionY: drift }} />
      <div style={{ position: "absolute", left: 0, right: 0, top: scanY, height: 4, background: `linear-gradient(90deg, transparent, ${SIGNAL}, transparent)`, boxShadow: `0 0 60px 14px rgba(255,194,75,.45)`, opacity: 0.85 }} />
      <AbsoluteFill style={{ background: "radial-gradient(1400px 1700px at 50% 45%, transparent 55%, rgba(4,10,26,.65) 100%)" }} />
    </AbsoluteFill>
  );
};

/** each slogan word rises out of its own mask, one after another; the lines are the ones layout.ts counted */
const Slogan: React.FC<{ lines: string[][]; m: Layout["slogan"]; lead?: number }> = ({ lines, m, lead = 0 }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  let n = 0;
  return (
    <div style={{ width: "100%", fontFamily: FONT_DISPLAY, fontWeight: 700, fontSize: m.font, lineHeight: `${m.lineH}px`, letterSpacing: m.spacing, color: PAPER, textAlign: "center" }}>
      {lines.map((ws, li) => (
        <div key={li} style={{ display: "flex", justifyContent: "center", columnGap: m.gap, height: m.pitch, whiteSpace: "nowrap" }}>
          {ws.map((w) => {
            const i = n++;
            const p = spring({ frame: f - i * 4 + lead, fps, config: { damping: 18, stiffness: 120 } });
            return (
              <span key={i} style={{ display: "inline-block", overflow: "hidden", paddingBottom: m.pad }}>
                <span style={{ display: "inline-block", transform: `translateY(${(1 - p) * 115}%)`, opacity: Math.min(1, p * 2) }}>{w}</span>
              </span>
            );
          })}
        </div>
      ))}
    </div>
  );
};

/** names the tool found on the screen, popping in like detections */
const Chips: React.FC<{ rows: string[][]; lead?: number }> = ({ rows, lead = 0 }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  let n = 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", rowGap: CHIPS.rowGap, marginTop: CHIPS.marginTop }}>
      {rows.map((row, ri) => (
        <div key={ri} style={{ display: "flex", justifyContent: "center", columnGap: CHIPS.gap }}>
          {row.map((c) => {
            const i = n++;
            const p = spring({ frame: f - 16 - i * 7 + lead, fps, config: { damping: 14, stiffness: 140 } });
            return (
              <span key={i} style={{ fontFamily: FONT_MONO, fontWeight: CHIPS.weight, fontSize: CHIPS.font, lineHeight: `${CHIPS.lineH}px`, whiteSpace: "nowrap", color: SIGNAL, padding: `${CHIPS.padV}px ${CHIPS.padH}px`, borderRadius: 999, background: "rgba(255,194,75,.12)", boxShadow: "inset 0 0 0 2px rgba(255,194,75,.5)", transform: `translateX(${(1 - p) * -90}px) scale(${0.7 + p * 0.3})`, opacity: p }}>
                {c}
              </span>
            );
          })}
        </div>
      ))}
    </div>
  );
};

/** a pulse for the call to action scene, which has no slogan */
const Pulse: React.FC<{ size: number }> = ({ size }) => {
  const f = useCurrentFrame();
  const k = size / PULSE.ref;
  return (
    <div style={{ position: "relative", width: size, height: size, margin: "0 auto", display: "grid", placeItems: "center" }}>
      {[0, 1, 2].map((i) => {
        const t = ((f + i * 18) % 54) / 54;
        return <div key={i} style={{ position: "absolute", width: (120 + t * 300) * k, height: (120 + t * 300) * k, borderRadius: "50%", border: `3px solid ${SIGNAL}`, opacity: 1 - t }} />;
      })}
      <div style={{ width: 120 * k, height: 120 * k, borderRadius: "50%", background: SIGNAL, boxShadow: "0 0 70px rgba(255,194,75,.7)" }} />
    </div>
  );
};

/** the spoken sentence in a glass card (outer shell, inner core), the current word lit; the lines are the ones layout.ts counted */
const Subtitle: React.FC<{ scene: Scene; lines: number[][]; m: Layout["caption"]; from: number; lead?: number }> = ({ scene, lines, m, from, lead = 0 }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const abs = (from + f) / fps;                                            // absolute time: the sequence starts on a whole frame
  const pop = spring({ frame: f + lead, fps, config: { damping: 20, stiffness: 110 } });
  return (
    <div style={{ boxSizing: "border-box", transform: `translateY(${(1 - pop) * 40}px)`, opacity: pop, padding: CAPTION.shell, borderRadius: 66, background: "rgba(255,255,255,.06)", boxShadow: "inset 0 0 0 2px rgba(143,180,255,.28)", width: m.width }}>
      <div style={{ boxSizing: "border-box", borderRadius: 56, padding: `${m.padV}px ${m.padH}px`, background: "linear-gradient(180deg, rgba(23,58,122,.78), rgba(12,28,64,.92))", boxShadow: "inset 0 2px 0 rgba(255,255,255,.14)", textAlign: "center", fontFamily: FONT_DISPLAY, fontWeight: CAPTION.weight, fontSize: m.font, lineHeight: `${m.lineH}px`, letterSpacing: m.spacing }}>
        {lines.map((ln, li) => (
          <div key={li} style={{ display: "flex", justifyContent: "center", columnGap: m.gap, height: m.lineH, whiteSpace: "nowrap" }}>
            {ln.map((i) => {
              const [w, a, b] = scene.words[i];
              const on = abs >= a && abs < b;
              const done = abs >= b;
              const emph = scene.emphasis.includes(clean(w));
              const k = on ? interpolate(abs, [a, a + 0.12], [1, 1.04], { extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) }) : 1;
              return (
                <span key={i} style={{ display: "inline-block", transform: `scale(${k})`, color: on || (emph && done) ? SIGNAL : done ? PAPER : MUTE, textShadow: on ? "0 0 40px rgba(255,194,75,.5)" : "none" }}>
                  {w}
                </span>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
};

/** one scene: slogan (or pulse) and chips on top, the caption card under them, each at the place layout.ts computed for this scene */
const SceneView: React.FC<{ scene: Scene; fit: Fitted; win: Win; tint: boolean; first?: boolean }> = ({ scene, fit, win, tint, first }) => {
  const f = useCurrentFrame();
  const L = fit.layout;
  // the scene is gone completely on its last frame (opacity 0 at f = dur - 1); the next one starts on the frame after. The last scene stays.
  const a = win.fadeFrom - win.from;
  const b = win.dur - 1;
  const out = win.last ? 1 : b > a ? interpolate(f, [a, b], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : f >= b ? 0 : 1;
  const lead = first ? 10 : 0;                                              // the first scene is already on its way at frame 0: that frame is the cover in the feed
  const into = first ? 1 : interpolate(f, [0, 5], [0, 1], { extrapolateRight: "clamp" });
  const opacity = out * into;
  const col = { position: "absolute" as const, left: L.safe.side, width: L.textW };
  return (
    <AbsoluteFill style={{ opacity, filter: `${tint ? TINT : ""}blur(${(1 - out) * 10}px)`, transform: `translateY(${(1 - out) * -24}px)` }}>
      <div style={{ ...col, top: L.slogan.top }}>
        {scene.screen ? <Slogan lines={fit.slogan} m={L.slogan} lead={lead} /> : <Pulse size={L.pulse ? L.pulse.size : PULSE.size} />}
        {fit.chips.length ? <Chips rows={fit.chips} lead={lead} /> : null}
      </div>
      <div style={{ ...col, top: L.caption.top }}>
        <Subtitle scene={scene} lines={fit.caption} m={L.caption} from={win.from} lead={lead} />
      </div>
    </AbsoluteFill>
  );
};

export const Reel: React.FC<ReelProps> = ({ scenes, total, audio, mouth, vis, mascot, safe, qaNoMascotArt, qaTint }) => {
  const f = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const now = f / fps;
  const zone = safe || SAFE;
  const mascotOn = mascot !== false;
  const fits = useMemo(
    () => scenes.map((s) => fitScene({ screen: s.screen || "", chips: s.chips, words: (s.words.length ? s.words.map((w) => w[0]) : s.text.split(/\s+/).filter(Boolean)) }, browserMeasure, { mascot: mascotOn, safe: zone })),
    [scenes, mascotOn, zone.top, zone.bottom, zone.side],
  );
  const wins = sceneWindows(scenes, fps, durationInFrames);
  const shown = wins.map((w, i) => (i === 0 ? { ...w, from: 0, dur: w.dur + w.from } : w));                 // the first scene is on screen from frame 0
  const size = mascotOn ? mascotSizeAt(f, wins, fits.map((x) => (x.layout.mascot ? x.layout.mascot.size : 0))) : 0;
  const bar = progressBar(zone);
  let emph = 0;                                                             // 1 right when an emphasised word starts, fading over half a second
  let scanning = false;                                                     // the current scene shows names found on a screen
  for (const s of scenes) {
    if (now >= s.start - 0.1 && now <= s.end + 0.2 && s.chips && s.chips.length) scanning = true;
    for (const [w, a] of s.words) {
      if (s.emphasis.includes(clean(w))) emph = Math.max(emph, interpolate(now - a, [-0.05, 0, 0.5], [0, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }));
    }
  }
  return (
    <AbsoluteFill style={{ backgroundColor: NAVY }}>
      <Background />
      {size >= 40 ? (
        <div style={{ position: "absolute", left: 0, right: 0, top: mascotTop(size, zone.top + 10), display: "flex", justifyContent: "center", visibility: qaNoMascotArt ? "hidden" : "visible" }}>
          <Mascot mouth={mouth || []} vis={vis || []} emph={emph} scanning={scanning} size={size} />
        </div>
      ) : null}
      {scenes.map((s, i) => (
        <Sequence key={i} from={shown[i].from} durationInFrames={shown[i].dur} layout="none">
          <SceneView scene={s} fit={fits[i]} win={shown[i]} tint={!!qaTint && i % 2 === 1} first={i === 0} />
        </Sequence>
      ))}
      <div style={{ position: "absolute", left: bar.x, top: bar.y, width: bar.w, height: bar.h, borderRadius: bar.h, background: "rgba(143,180,255,.2)" }}>
        <div style={{ width: `${Math.min(1, f / fps / total) * 100}%`, height: "100%", borderRadius: bar.h, background: SIGNAL }} />
      </div>
      {audio ? <Audio src={staticFile(audio)} /> : null}
    </AbsoluteFill>
  );
};
