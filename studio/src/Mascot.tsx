import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

const SIGNAL = "#FFC24B";
const LINE = "#8FB4FF";

export type MascotProps = {
  vis: number[];            // mouth shape per frame: 0 rest, 1 A, 2 E, 3 I, 4 O, 5 U, 6 M/B/P, 7 F/V, 8 S/Z, 9 other
  mouth: number[];          // how loud the voice is per frame, 0..1
  emph: number;             // 0..1: an emphasised word was just spoken
  scanning: boolean;        // the scene is about something found on a screen
  size?: number;
};

/** mouth shapes of an LED face: width, height, roundness, and whether teeth show. `m` is the loudness. */
const shape = (v: number, m: number) => {
  switch (v) {
    case 1: return { w: 92, h: 26 + m * 74, r: 30, teeth: false };           // A: tall
    case 2: return { w: 124, h: 16 + m * 44, r: 14, teeth: false };          // E: wide
    case 3: return { w: 138, h: 10 + m * 18, r: 8, teeth: true };            // I: wide and thin
    case 4: return { w: 70, h: 38 + m * 56, r: 36, teeth: false };           // O: round
    case 5: return { w: 44, h: 28 + m * 32, r: 22, teeth: false };           // U: small and round
    case 6: return { w: 84, h: 8, r: 4, teeth: false };                      // M B P: lips closed
    case 7: return { w: 104, h: 14, r: 6, teeth: true };                     // F V: teeth on the lip
    case 8: return { w: 114, h: 13 + m * 10, r: 6, teeth: true };            // S Z: teeth together
    case 9: return { w: 86, h: 18 + m * 38, r: 16, teeth: false };           // L T D N and the rest
    default: return { w: 70, h: 8, r: 4, teeth: false };
  }
};

/** the mascot: a small robot whose face is a screen. Alive through blinking, breathing, a lagging antenna, mouth shapes that follow the sounds,
 *  eyebrows and a hand gesture on emphasised words, and scanning brackets when the scene is about something read from a screen. */
export const Mascot: React.FC<MascotProps> = ({ vis, mouth, emph, scanning, size = 420 }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const at = (a: number[], i: number) => a[Math.max(0, Math.min(a.length - 1, i))] || 0;
  const enter = spring({ frame: f, fps, config: { damping: 16, stiffness: 90 } });
  const bob = Math.sin(f / 17) * 9 + Math.sin(f / 5) * 1.2 * at(mouth, f);
  const tilt = Math.sin(f / 43) * 2.4 + emph * 2.5;
  const cyc = f % 104;                                                         // a blink about every 3.5 s
  const open = cyc < 5 ? Math.abs(cyc - 2.5) / 2.5 : 1;
  const m = at(mouth, f);
  const pulse = 0.55 + 0.45 * Math.sin(f / 7);
  const scan = ((f * 2.6) % 260) - 20;
  const squash = 1 + m * 0.02 + emph * 0.035;
  // eyes: bigger on emphasis, a glance down while scanning, a slow wander otherwise
  const lookX = scanning ? Math.sin(f / 9) * 14 : Math.sin(f / 61) * 8;
  const lookY = scanning ? 8 : Math.sin(f / 83) * 3;
  const eyeH = (18 + 50 * open) * (1 + emph * 0.22);
  const eyeW = 46 * (1 + emph * 0.12);
  const brow = emph * 16 + (scanning ? 4 : 0);                                 // eyebrows rise on emphasis
  // mouth: shapes of the three neighbouring frames are averaged so it moves like a mouth, not like a slideshow
  const sh = [-1, 0, 1].map((d) => shape(at(vis, f + d), at(mouth, f + d)));
  const mv = { w: (sh[0].w + sh[1].w * 2 + sh[2].w) / 4, h: (sh[0].h + sh[1].h * 2 + sh[2].h) / 4, r: (sh[0].r + sh[1].r * 2 + sh[2].r) / 4 };
  const speaking = m > 0.05 && at(vis, f) !== 0;
  const teeth = sh[1].teeth && speaking;
  // arms: a wave when the video starts, then a small beat with the right hand on every emphasised word
  const wave = interpolate(f, [0, 12, 70, 90], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const armL = 18 + wave * (140 + Math.sin(f / 2.6) * 20) + Math.sin(f / 31) * 3;
  const armR = -18 - emph * 38 - Math.sin(f / 37) * 3;
  const antX = 260 + Math.sin(f / 11) * 11 + tilt * 2;                         // the antenna bulb lags behind the head
  const bracket = scanning ? 0.5 + 0.5 * Math.sin(f / 5) : 0;
  return (
    <div style={{ width: size * (660 / 520), height: size * (560 / 520), transform: `translateY(${(1 - enter) * 420 + bob}px) rotate(${tilt}deg) scale(${squash}, ${1 + (1 - squash) * 0.6})`, transformOrigin: "50% 92%", opacity: Math.min(1, enter * 2) }}>
      <svg viewBox="-70 0 660 560" width="100%" height="100%">
        <defs>
          <linearGradient id="core" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#1f52a6" /><stop offset="1" stopColor="#0c1c40" /></linearGradient>
          <linearGradient id="body" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#1a4388" /><stop offset="1" stopColor="#0d2250" /></linearGradient>
          <linearGradient id="shine" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#fff" stopOpacity="0.28" /><stop offset="1" stopColor="#fff" stopOpacity="0" /></linearGradient>
          <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="7" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          <filter id="drop" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="14" stdDeviation="14" floodColor="#02060f" floodOpacity="0.55" /></filter>
          <clipPath id="screen"><rect x="120" y="150" width="280" height="200" rx="44" /></clipPath>
        </defs>

        {/* arms behind the body */}
        <g transform={`translate(128 500) rotate(${armL})`}>
          <rect x="-19" y="-6" width="38" height="104" rx="19" fill="#173A7A" stroke="rgba(143,180,255,.35)" strokeWidth="3" />
          <circle cx="0" cy="104" r="28" fill="#1f52a6" stroke="rgba(143,180,255,.4)" strokeWidth="3" />
        </g>
        <g transform={`translate(392 500) rotate(${armR})`}>
          <rect x="-19" y="-6" width="38" height="104" rx="19" fill="#173A7A" stroke="rgba(143,180,255,.35)" strokeWidth="3" />
          <circle cx="0" cy="104" r="28" fill="#1f52a6" stroke="rgba(143,180,255,.4)" strokeWidth="3" />
        </g>

        {/* antenna */}
        <path d={`M260 104 Q${260 + (antX - 260) * 0.4} 74 ${antX} 50`} stroke={LINE} strokeWidth="6" strokeLinecap="round" fill="none" opacity="0.75" />
        <circle cx={antX} cy="40" r="17" fill={SIGNAL} filter="url(#glow)" opacity={0.6 + 0.4 * (speaking ? pulse : 0.6)} />

        {/* shoulders and neck */}
        <rect x="222" y="396" width="76" height="40" rx="10" fill="#12295A" />
        <path d="M118 560 L118 520 Q118 440 200 436 L320 436 Q402 440 402 520 L402 560 Z" fill="url(#body)" stroke="rgba(143,180,255,.3)" strokeWidth="3" filter="url(#drop)" />
        <circle cx="260" cy="494" r="11" fill={SIGNAL} filter="url(#glow)" opacity={speaking ? 0.5 + 0.5 * pulse : 0.45} />

        {/* ears */}
        <rect x="38" y="214" width="38" height="92" rx="19" fill="#173A7A" stroke="rgba(143,180,255,.35)" strokeWidth="3" />
        <rect x="444" y="214" width="38" height="92" rx="19" fill="#173A7A" stroke="rgba(143,180,255,.35)" strokeWidth="3" />

        {/* head: outer shell, inner core, a soft highlight, the screen */}
        <rect x="70" y="100" width="380" height="304" rx="86" fill="rgba(255,255,255,.07)" stroke="rgba(143,180,255,.4)" strokeWidth="3" filter="url(#drop)" />
        <rect x="84" y="114" width="352" height="276" rx="74" fill="url(#core)" />
        <path d="M110 190 Q110 128 190 124 L330 124 Q410 128 410 190 Q330 150 260 150 Q190 150 110 190Z" fill="url(#shine)" />
        <rect x="120" y="150" width="280" height="200" rx="44" fill="#071026" stroke="rgba(143,180,255,.3)" strokeWidth="3" />
        <g clipPath="url(#screen)">
          <rect x="120" y={140 + scan} width="280" height="5" fill={SIGNAL} opacity="0.25" />
          <rect x="120" y={140 + scan - 18} width="280" height="36" fill={SIGNAL} opacity="0.05" />
        </g>

        {/* scanning brackets: the robot is looking at something on a screen */}
        {scanning ? (
          <g stroke={SIGNAL} strokeWidth="6" strokeLinecap="round" fill="none" opacity={0.5 + bracket * 0.5}>
            <path d="M132 188 V162 Q132 162 158 162" /><path d="M388 188 V162 Q388 162 362 162" />
            <path d="M132 312 V338 Q132 338 158 338" /><path d="M388 312 V338 Q388 338 362 338" />
          </g>
        ) : null}

        {/* eyebrows, eyes */}
        <rect x={188 + lookX * 0.4} y={186 - brow} width="34" height="7" rx="3.5" fill={SIGNAL} opacity="0.85" transform={`rotate(${-emph * 8} 205 190)`} />
        <rect x={298 + lookX * 0.4} y={186 - brow} width="34" height="7" rx="3.5" fill={SIGNAL} opacity="0.85" transform={`rotate(${emph * 8} 315 190)`} />
        <rect x={182 - (eyeW - 46) / 2 + lookX} y={232 + lookY - eyeH / 2} width={eyeW} height={eyeH} rx="23" fill={SIGNAL} filter="url(#glow)" />
        <rect x={292 - (eyeW - 46) / 2 + lookX} y={232 + lookY - eyeH / 2} width={eyeW} height={eyeH} rx="23" fill={SIGNAL} filter="url(#glow)" />

        {/* mouth */}
        {speaking ? (
          <g filter="url(#glow)">
            <rect x={260 - mv.w / 2} y={304 - mv.h / 2} width={mv.w} height={mv.h} rx={Math.min(mv.r, mv.h / 2)} fill={SIGNAL} />
            {teeth ? <rect x={260 - mv.w / 2 + 8} y={304 - mv.h / 2 + 2} width={mv.w - 16} height={Math.max(3, mv.h * 0.38)} rx="2" fill="#F2EEE3" opacity="0.95" /> : null}
          </g>
        ) : (
          <path d={`M208 ${300 + emph * 2} Q260 ${334 + emph * 8} 312 ${300 + emph * 2}`} fill="none" stroke={SIGNAL} strokeWidth="11" strokeLinecap="round" filter="url(#glow)" />
        )}
      </svg>
    </div>
  );
};
