"""ears.py - lets the checker HEAR a reel: a sound-event model (Audio Spectrogram Transformer trained on AudioSet, Apache-2.0) labels every
stretch of the sound with what it recognises: speech, music, breathing, sigh, noise, hiss, click, silence...

It runs INSIDE the environment that has torch and transformers (~/.reel-intel/venv-tts, the same one Chatterbox uses), as a subprocess:

  python ears.py INPUT.wav OUTPUT.json [WINDOW_SECONDS] [HOP_SECONDS]

OUTPUT.json: {"window": 1.0, "hop": 0.5, "windows": [{"t": 0.0, "labels": [["Speech", 0.91], ["Music", 0.2], ...]}]}
The model is a classifier, not a listener with taste: it says WHAT is there, never whether it sounds pleasant. Labels with a low score are noise.
"""
import json
import sys
import wave

MODEL = "MIT/ast-finetuned-audioset-10-10-0.4593"
# AudioSet labels that mean a breath, a mouth noise or a body sound: never wanted in a voice-over
BREATH_LABELS = {"Breathing", "Gasp", "Sigh", "Snort", "Pant", "Wheeze", "Sniff", "Grunt", "Groan", "Whimper", "Cough", "Throat clearing", "Hiccup",
                 "Burping, eructation", "Snoring", "Oink", "Slurp", "Lip smacking"}
NOISE_LABELS = {"White noise", "Static", "Hiss", "Noise", "Crackle", "Clicking", "Click", "Pop", "Thump, thud", "Rumble", "Buzz", "Hum"}


def breath_score(labels, wanted=BREATH_LABELS):
    """highest score of any unwanted-body-sound label among a window's labels, and which one"""
    best = (0.0, "")
    for name, p in labels:
        if name in wanted and p > best[0]:
            best = (p, name)
    return best


def load(path):
    import numpy as np
    with wave.open(path) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
        if w.getnchannels() > 1:
            x = x[::w.getnchannels()]
    if sr != 16000:
        import torch
        import torchaudio
        x = torchaudio.functional.resample(torch.from_numpy(x), sr, 16000).numpy()
    return x


def classify(model, fe, dev, x, win, hop):
    import numpy as np
    import torch
    n, w, h = len(x), int(win * 16000), int(hop * 16000)
    starts = list(range(0, max(1, n - w + 1), h)) or [0]
    res = []
    for i in range(0, len(starts), 16):
        chunk = [x[s:s + w] for s in starts[i:i + 16]]
        inp = fe(chunk, sampling_rate=16000, return_tensors="pt").to(dev)
        with torch.no_grad():
            p = torch.sigmoid(model(**inp).logits).cpu().numpy()
        for s, row in zip(starts[i:i + 16], p):
            top = np.argsort(-row)[:8]
            res.append({"t": round(s / 16000, 2), "labels": [[model.config.id2label[int(k)], round(float(row[k]), 3)] for k in top]})
    return res


def score_files():
    """python ears.py --score LIST.json OUT.json : LIST.json is a list of wav paths; OUT.json maps each path to its worst breath-like window"""
    import torch
    from transformers import ASTFeatureExtractor, ASTForAudioClassification
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    fe = ASTFeatureExtractor.from_pretrained(MODEL)
    model = ASTForAudioClassification.from_pretrained(MODEL).to(dev).eval()
    out = {}
    for path in json.load(open(sys.argv[2], encoding="utf-8")):
        try:
            wins = classify(model, fe, dev, load(path), 0.8, 0.4)
        except Exception as e:
            out[path] = {"error": str(e)}
            continue
        worst = max(((breath_score(w["labels"]), w["t"]) for w in wins), key=lambda z: z[0][0])
        out[path] = {"breath": worst[0][0], "label": worst[0][1], "t": worst[1], "mean": sum(breath_score(w["labels"])[0] for w in wins) / max(len(wins), 1)}
    json.dump(out, open(sys.argv[3], "w", encoding="utf-8"), ensure_ascii=False)
    print("ok", len(out), "files")


def main():
    if sys.argv[1] == "--score":
        return score_files()
    src, out = sys.argv[1], sys.argv[2]
    win = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    hop = float(sys.argv[4]) if len(sys.argv) > 4 else 0.5
    import numpy as np
    import torch
    from transformers import ASTFeatureExtractor, ASTForAudioClassification
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    fe = ASTFeatureExtractor.from_pretrained(MODEL)
    model = ASTForAudioClassification.from_pretrained(MODEL).to(dev).eval()
    names = model.config.id2label
    x = load(src)
    n, w, h = len(x), int(win * 16000), int(hop * 16000)
    res = []
    starts = list(range(0, max(1, n - w + 1), h)) or [0]
    for i in range(0, len(starts), 16):                                         # batches of 16 windows
        chunk = [x[s:s + w] for s in starts[i:i + 16]]
        inp = fe(chunk, sampling_rate=16000, return_tensors="pt").to(dev)
        with torch.no_grad():
            p = torch.sigmoid(model(**inp).logits).cpu().numpy()
        for s, row in zip(starts[i:i + 16], p):
            top = np.argsort(-row)[:8]
            res.append({"t": round(s / 16000, 2), "labels": [[names[int(k)], round(float(row[k]), 3)] for k in top]})
    json.dump({"window": win, "hop": hop, "windows": res}, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print("ok", len(res), "windows")


if __name__ == "__main__":
    main()
