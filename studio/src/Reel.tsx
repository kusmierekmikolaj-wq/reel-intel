import React from "react";
import { Mascot } from "./Mascot";
import { AbsoluteFill, Audio, Easing, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";

export type Scene = {
  text: string;
  screen: string;
  emphasis: string[];
  chips?: string[];
  start: number;
  end: number;
  words: [string, number, number][];
};
export type ReelProps = { scenes: Scene[]; total: number; audio?: string; mouth?: number[]; vis?: number[]; mascot?: boolean };

const NAVY = "#0A1733";
const PAPER = "#F2EEE3";
const SIGNAL = "#FFC24B";
const MUTE = "#9FB0D6";
const LINE = "#8FB4FF";
const DISPLAY = '"Bahnschrift", "Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif';
const MONO = '"Cascadia Mono", Consolas, monospace';
const GAP = 0.28;
const clean = (w: string) => w.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");

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

/** each slogan word rises out of its own mask, one after another */
const Slogan: React.FC<{ text: string }> = ({ text }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const words = text.split(/\s+/).filter(Boolean);
  return (
    <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: "0 34px", padding: "0 80px", fontFamily: DISPLAY, fontWeight: 700, fontSize: words.join(" ").length > 22 ? 118 : 138, lineHeight: 1.04, letterSpacing: -4, color: PAPER, textAlign: "center" }}>
      {words.map((w, i) => {
        const p = spring({ frame: f - i * 4, fps, config: { damping: 18, stiffness: 120 } });
        return (
          <span key={i} style={{ display: "inline-block", overflow: "hidden", paddingBottom: 18 }}>
            <span style={{ display: "inline-block", transform: `translateY(${(1 - p) * 115}%)`, opacity: Math.min(1, p * 2) }}>{w}</span>
          </span>
        );
      })}
    </div>
  );
};

/** names the tool found on the screen, popping in like detections */
const Chips: React.FC<{ chips: string[] }> = ({ chips }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: 18, padding: "0 90px", marginTop: 54 }}>
      {chips.map((c, i) => {
        const p = spring({ frame: f - 16 - i * 7, fps, config: { damping: 14, stiffness: 140 } });
        return (
          <span key={i} style={{ fontFamily: MONO, fontWeight: 600, fontSize: 46, color: SIGNAL, padding: "16px 28px", borderRadius: 999, background: "rgba(255,194,75,.12)", boxShadow: "inset 0 0 0 2px rgba(255,194,75,.5)", transform: `translateX(${(1 - p) * -90}px) scale(${0.7 + p * 0.3})`, opacity: p }}>
            {c}
          </span>
        );
      })}
    </div>
  );
};

/** a pulse for the call to action scene, which has no slogan */
const Pulse: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <div style={{ position: "relative", width: 420, height: 420, display: "grid", placeItems: "center" }}>
      {[0, 1, 2].map((i) => {
        const t = ((f + i * 18) % 54) / 54;
        return <div key={i} style={{ position: "absolute", width: 120 + t * 300, height: 120 + t * 300, borderRadius: "50%", border: `3px solid ${SIGNAL}`, opacity: 1 - t }} />;
      })}
      <div style={{ width: 120, height: 120, borderRadius: "50%", background: SIGNAL, boxShadow: "0 0 70px rgba(255,194,75,.7)" }} />
    </div>
  );
};

/** the spoken sentence in a glass card (outer shell, inner core), the current word lit */
const Subtitle: React.FC<{ scene: Scene }> = ({ scene }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const abs = Math.max(0, Math.floor((scene.start - 0.1) * fps)) / fps + f / fps;   // absolute time: the sequence starts on a whole frame
  const pop = spring({ frame: f, fps, config: { damping: 20, stiffness: 110 } });
  return (
    <div style={{ transform: `translateY(${(1 - pop) * 70}px)`, opacity: pop, padding: 12, borderRadius: 66, background: "rgba(255,255,255,.06)", boxShadow: "inset 0 0 0 2px rgba(143,180,255,.28)", width: 940 }}>
      <div style={{ borderRadius: 56, padding: "46px 52px", background: "linear-gradient(180deg, rgba(23,58,122,.78), rgba(12,28,64,.92))", boxShadow: "inset 0 2px 0 rgba(255,255,255,.14)", textAlign: "center", fontFamily: DISPLAY, fontWeight: 600, fontSize: 68, lineHeight: 1.28, letterSpacing: -1 }}>
        {scene.words.map(([w, a, b], i) => {
          const on = abs >= a && abs < b;
          const done = abs >= b;
          const emph = scene.emphasis.includes(clean(w));
          const k = on ? interpolate(abs, [a, a + 0.12], [1, 1.04], { extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) }) : 1;
          return (
            <span key={i} style={{ display: "inline-block", marginRight: 24, transform: `scale(${k})`, color: on || (emph && done) ? SIGNAL : done ? PAPER : MUTE, textShadow: on ? "0 0 40px rgba(255,194,75,.5)" : "none" }}>
              {w}
            </span>
          );
        })}
      </div>
    </div>
  );
};

const SceneView: React.FC<{ scene: Scene; last: boolean; mascotOn: boolean }> = ({ scene, last, mascotOn }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const len = Math.ceil((scene.end + GAP - scene.start + 0.1) * fps);
  const out = interpolate(f, [len - 8, len], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const opacity = last ? 1 : out;
  return (
    <AbsoluteFill style={{ opacity, filter: `blur(${(1 - opacity) * 10}px)`, transform: `translateY(${(1 - opacity) * -40}px)` }}>
      <div style={{ position: "absolute", left: 0, right: 0, top: mascotOn ? 720 : 300, display: "flex", flexDirection: "column", alignItems: "center", minHeight: mascotOn ? 400 : 760, justifyContent: "center" }}>
        {scene.screen ? <Slogan text={scene.screen} /> : <Pulse />}
        {scene.chips && scene.chips.length ? <Chips chips={scene.chips} /> : null}
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, top: 1130, display: "flex", justifyContent: "center" }}>
        <Subtitle scene={scene} />
      </div>
    </AbsoluteFill>
  );
};

export const Reel: React.FC<ReelProps> = ({ scenes, total, audio, mouth, vis, mascot }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const now = f / fps;
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
      {mascot !== false ? (
        <div style={{ position: "absolute", left: 0, right: 0, top: 110, display: "flex", justifyContent: "center" }}>
          <Mascot mouth={mouth || []} vis={vis || []} emph={emph} scanning={scanning} size={520} />
        </div>
      ) : null}
      {scenes.map((s, i) => {
        const from = Math.max(0, Math.floor((s.start - 0.1) * fps));
        const dur = Math.ceil((s.end + GAP - s.start + 0.1) * fps);
        return (
          <Sequence key={i} from={from} durationInFrames={dur} layout="none">
            <SceneView scene={s} last={i === scenes.length - 1} mascotOn={mascot !== false} />
          </Sequence>
        );
      })}
      <div style={{ position: "absolute", left: 90, top: 1690, width: 900, height: 8, borderRadius: 8, background: "rgba(143,180,255,.2)" }}>
        <div style={{ width: `${Math.min(1, f / fps / total) * 100}%`, height: "100%", borderRadius: 8, background: SIGNAL }} />
      </div>
      {audio ? <Audio src={staticFile(audio)} /> : null}
    </AbsoluteFill>
  );
};
