"""profile.py - the thresholds. DEFAULT is built from what the data and the standards say (see docs/QA.md for every source and how sure it is);
`reel-intel make calibrate` writes a calibrated qa_profile.json that overrides the defaults key by key. Detectors read thresholds from ctx.profile
and never hard-code a number."""
import copy
import json
import os
from pathlib import Path

DEFAULT = {
    "version": 1,
    "pace": {"fast": 6.8, "slow": 3.2, "target": 5.8, "burst": 9.0},                # syllables a second; real speakers in our data: 2.9-6.5
    "gap": {"long_s": 0.9, "short_s": 0.12, "loud_db_under_speech": 8, "rumble_share": 0.45, "rumble_floor_db": -42},
    "voice": {"heard_warn": 0.8, "heard_error": 0.5, "last_word_max_s": 1.1, "inner_pause_s": 0.6, "inner_pause_warn_s": 0.9, "level_jump_db": 6, "hiss_db": -46,
              "breath_score": 0.07, "soft_tail_s": 0.12},
    "intonation": {"f0_std_semitones_min": None, "pitch_jump_semitones": 12},        # None until calibrated on real speech
    "mix": {"voice_to_music_db_min": 14, "clip_share": 0.0005, "pumping_db_max": 3.0},
    "loudness": {"target_lufs": -16.0, "tolerance": 2.5, "true_peak_max": -1.0, "uncertain": True},   # sources differ (-10..-16); calibrate on the reference account
    "platform": {"width": 1080, "height": 1920, "fps": 30, "min_s": 3, "max_s": 90, "codec": ["h264", "hevc"], "pix_fmt": "yuv420p"},
    "safe_zone": {"top": 270, "bottom": 440, "side": 90, "uncertain": True},          # sources disagree (top 200-270, bottom 350-672, sides 65-130)
    "captions": {"max_cps": 20, "min_show_s": 0.8, "max_lines": 3, "min_px": 48, "min_contrast": 4.5},
    "sync": {"audio_early_ms": 45, "audio_late_ms": 60, "caption_ms": 80},
    "picture": {"black_s": 0.5, "freeze_s": 2.0, "first_frame_dark": 0.05, "flash_per_s": 3},
}


def profile_path():
    """the calibrated profile file: $REEL_QA_PROFILE, else qa_profile.json next to this file"""
    return Path(os.environ.get("REEL_QA_PROFILE") or Path(__file__).with_name("qa_profile.json"))


def load(path=None):
    """DEFAULT with the keys of a calibrated profile file laid over it (one level deep)"""
    prof = copy.deepcopy(DEFAULT)
    p = Path(path) if path else profile_path()
    if p.is_file():
        try:
            for k, v in json.loads(p.read_text(encoding="utf-8")).items():
                if isinstance(v, dict) and isinstance(prof.get(k), dict):
                    prof[k].update(v)
                else:
                    prof[k] = v
        except Exception:
            pass
    return prof
