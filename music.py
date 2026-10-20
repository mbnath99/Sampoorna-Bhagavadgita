"""
Background music for the Gita reels.

If the music/ folder has flute tracks (mp3/m4a/wav), reel N uses track N (cycling).
Otherwise a soft built-in bansuri melody (raga Bhupali) over a tanpura drone is
synthesised, different for every reel.
"""
import os, subprocess
import numpy as np

SR = 44100
HERE = os.path.dirname(os.path.abspath(__file__))
EXT = (".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac")

def tracks(folder="music"):
    d = folder if os.path.isabs(folder) else os.path.join(HERE, folder)
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(EXT)) if os.path.isdir(d) else []

def load_track(path, seconds):
    """Decode a track, loop it if shorter than the reel, fade in/out."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-stream_loop", "-1", "-i", path, "-t", str(seconds),
                          "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768
    return x / (np.max(np.abs(x)) or 1)

# ------------------------------------------------------------------ built-in bansuri
SA = 261.63 * 0.75                       # Sa ≈ G3 (a typical medium bansuri key)
BHUPALI = [0, 2, 4, 7, 9]                # Sa Re Ga Pa Dha

def _note(f, dur, rng, amp=1.0):
    n = int(dur * SR); t = np.arange(n) / SR
    vib = 1 + 0.006 * np.sin(2 * np.pi * 5.2 * t) * np.clip((t - 0.25) / 0.4, 0, 1)   # vibrato after attack
    ph = 2 * np.pi * np.cumsum(f * vib) / SR
    tone = np.sin(ph) + 0.18 * np.sin(2 * ph) + 0.06 * np.sin(3 * ph)
    breath = np.convolve(rng.normal(0, 1, n), np.ones(30) / 30, mode="same") * 0.08
    env = np.minimum(1, t / 0.12) * np.minimum(1, (dur - t) / 0.35).clip(0, 1)
    return (tone + breath) * env * amp

def comb(x, d, g):
    """Feedback echo y[n] = x[n] + g*y[n-d], computed a block at a time (fast)."""
    y = x.copy()
    for k in range(d, len(y), d):
        e = min(k + d, len(y))
        y[k:e] += g * y[k - d:e - d]
    return y

def _drone(seconds):
    n = int(seconds * SR); t = np.arange(n) / SR
    s = np.zeros(n)
    for f, a in ((SA / 2, 1.0), (SA * 0.75, 0.5), (SA, 0.6)):
        for k in range(1, 6):
            s += a / k * np.sin(2 * np.pi * f * k * t) * (1 + 0.2 * np.sin(2 * np.pi * (0.13 + 0.05 * k) * t))
    return s / 6

def bansuri(seconds, seed=0):
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    out = np.zeros(n + SR * 4)
    t = 1.5
    deg = 0
    while t < seconds - 2:
        phrase_len = rng.integers(3, 7)
        for _ in range(phrase_len):
            deg = int(np.clip(deg + rng.choice([-2, -1, -1, 1, 1, 2]), -3, 7))
            octave, idx = divmod(deg, 5)
            f = SA * 2 ** ((BHUPALI[idx] + 12 * octave) / 12) * 2
            dur = float(rng.choice([0.6, 0.9, 1.2, 1.8]))
            seg = _note(f, dur, rng, 0.5)
            a = int(t * SR); out[a:a + len(seg)] += seg[:len(out) - a]
            t += dur * 0.92
        t += float(rng.uniform(1.5, 3.5))           # breathing space between phrases
    out = out[:n]
    rev = sum(comb(out, d, g) for d, g in ((1931, 0.5), (2711, 0.45), (3527, 0.4)))
    mix = out * 0.7 + rev * 0.18 + _drone(seconds) * 0.12
    return mix / (np.max(np.abs(mix)) or 1)

def background(seconds, reel_no, cfg):
    mcfg = cfg.get("music", {})
    if mcfg.get("source", "auto") != "builtin":
        tr = tracks(mcfg.get("folder", "music"))
        if tr:
            path = tr[(reel_no - 1) % len(tr)]
            print("music:", os.path.basename(path))
            return load_track(path, seconds)
    print("music: built-in bansuri")
    return bansuri(seconds, seed=reel_no)
