"""Runs INSIDE the separate environment that has Chatterbox installed (~/.reel-intel/venv-tts); reel-intel calls it as a subprocess.

  python chatterbox_cli.py JOBS.json

JOBS.json: {"lang": "pl", "ref": "voice sample or null", "exaggeration": 0.5, "cfg": 0.5, "takes": 1, "seed": 1000, "jobs": [{"text": "...", "out": "file.wav"}]}
The model is loaded once and every job is synthesised one sentence at a time. It imports nothing from reel_intel.
"""
import json
import sys


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    import torch
    import torchaudio as ta
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    model = ChatterboxMultilingualTTS.from_pretrained(device="cuda" if torch.cuda.is_available() else "cpu")
    takes = int(spec.get("takes", 1))
    for j in spec["jobs"]:
        for k in range(takes):                                                  # several takes: the caller keeps the one whose words are heard best
            torch.manual_seed(int(spec.get("seed", 1000)) + k)
            wav = model.generate(j["text"], language_id=spec.get("lang", "pl"), audio_prompt_path=spec.get("ref") or None,
                                 exaggeration=float(spec.get("exaggeration", 0.5)), cfg_weight=float(spec.get("cfg", 0.5)))
            out = j["out"] if takes == 1 else j["out"].replace(".wav", f"_t{k}.wav")
            ta.save(out, wav, model.sr, encoding="PCM_S", bits_per_sample=16)
            print("done:", out, flush=True)


if __name__ == "__main__":
    main()
