#!/usr/bin/env python3
"""
Bhagavad Gita reel renderer: 3 shlokas per reel, 1080x1920.

  - background: a Sri Krishna image from images/ (one per reel, cycling), slow zoom
  - top right: reel serial number and chapter.verse range  (#12 · 1.4–6)
  - each shloka types out while the Sarvam voice recites it, then its అనువాదము
  - flute music (music/ folder, or built-in bansuri), lowered while the voice speaks

CLI:  python render.py 1                  -> out/reel-001.mp4/.jpg/.txt
      python render.py 1 --no-voice       (music only, timing estimated)
"""
import argparse, json, math, os, subprocess, wave
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
import sarvam, music

HERE = os.path.dirname(os.path.abspath(__file__))
FPS, SR = 30, 44100
W, H = 1080, 1920
GOLD = (240, 196, 98)
CREAM = (255, 246, 226)
DIM = (236, 224, 205)
GREY = (200, 185, 165)

def load_config():
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        return json.load(f)

def load_book():
    with open(os.path.join(HERE, "data", "gita.json"), encoding="utf-8") as f:
        return json.load(f)

# ------------------------------------------------------------------ reels: 3 entries each, never across chapters
def build_reels(book, per_reel=3):
    reels = []
    for ch in book["chapters"]:
        ents = ch["entries"]
        for i in range(0, len(ents), per_reel):
            group = ents[i:i + per_reel]
            if len(group) == 1 and reels and reels[-1]["chapter"] == ch["n"] and len(reels[-1]["entries"]) < per_reel + 1:
                reels[-1]["entries"] += group           # a single leftover shloka joins the previous reel
                continue
            reels.append({"chapter": ch["n"], "entries": group})
    for k, r in enumerate(reels, 1):
        r["serial"] = k
        r["first"], r["last"] = r["entries"][0]["verses"][0], r["entries"][-1]["verses"][-1]
        r["ref"] = f"{r['chapter']}.{r['first']}" + (f"–{r['last']}" if r["last"] != r["first"] else "")
    return reels

def entry_ref(ch, e):
    v = e["verses"]
    return f"{ch}.{v[0]}" + (f"–{v[-1]}" if len(v) > 1 else "")

# ------------------------------------------------------------------ fonts / text
_fc = {}
def font(name, size, weight=None):
    key = (name, size, weight)
    if key not in _fc:
        f = ImageFont.truetype(os.path.join(HERE, "fonts", name), size, layout_engine=ImageFont.Layout.RAQM)
        if weight:
            try:
                f.set_variation_by_axes([weight if a["name"] in (b"Weight", "Weight") else a["default"] for a in f.get_variation_axes()])
            except Exception:
                pass
        _fc[key] = f
    return _fc[key]
SERIF, SANS, DISPLAY = "NotoSerifTelugu.ttf", "NotoSansTelugu.ttf", "Suravaram.ttf"

def tw(t, f):
    l, _, r, _ = f.getbbox(t); return r - l

def wrap(text, f, max_w):
    out, cur = [], ""
    text = text.replace(" ।", "\u00a0।").replace(" ॥", "\u00a0॥")      # keep dandas with their word
    for w in [x for x in text.split(" ") if x]:
        trial = (cur + " " + w).strip()
        if tw(trial, f) <= max_w or not cur: cur = trial
        else: out.append(cur); cur = w
    if cur: out.append(cur)
    return [l.replace("\u00a0", " ") for l in out]

_MARKS = set(range(0x0C00, 0x0C05)) | set(range(0x0C3C, 0x0C57)) | {0x0C62, 0x0C63, 0x200C, 0x200D}
def aksharas(text):
    out = []
    for ch in text:
        cp = ord(ch)
        if out and (cp in _MARKS or ord(out[-1][-1]) == 0x0C4D and 0x0C15 <= cp <= 0x0C39):
            out[-1] += ch
        else:
            out.append(ch)
    return out

def shadowed(img, blur=7, strength=0.85):
    sh = Image.new("RGBA", img.size, (0, 0, 0, 255))
    sh.putalpha(img.getchannel("A").filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * strength)))
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.alpha_composite(sh, (0, 3)); out.alpha_composite(img)
    return out

def line_sprite(full, prefix, f, fill, width):
    asc, desc = f.getmetrics()
    img = Image.new("RGBA", (width, asc + desc + 24), (0, 0, 0, 0))
    x0 = (width - tw(full, f)) / 2
    if prefix:
        ImageDraw.Draw(img).text((x0, 12), prefix, font=f, fill=fill)
        img = shadowed(img)
    return img

def ease(x):
    x = min(1, max(0, x)); return 1 - (1 - x) ** 3

def with_alpha(img, a):
    if a >= 1: return img
    s = img.copy(); s.putalpha(img.getchannel("A").point(lambda v: int(v * a))); return s

# ------------------------------------------------------------------ background image
def pick_image(serial, cfg):
    """The image named by the reel number (1.jpg, 01.jpg, 001.jpg, 'reel 12.png' -> reel 12).
    Reels without their own image reuse the numbered images in number order (1, 2, 3 ... 10, 11), cycling."""
    d = os.path.join(HERE, cfg.get("images_folder", "images"))
    imgs = [f for f in os.listdir(d) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))] if os.path.isdir(d) else []
    if not imgs:
        return None
    exact = sorted(f for f in imgs if voice_number(f) == serial)
    if exact:
        return os.path.join(d, exact[0])
    imgs.sort(key=lambda f: (voice_number(f) is None, voice_number(f) or 0, f.lower()))
    return os.path.join(d, imgs[(serial - 1) % len(imgs)])

def placeholder_bg():
    y = np.linspace(0, 1, H)[:, None, None]; x = np.linspace(-1, 1, W)[None, :, None]
    top, bot = np.array([18, 32, 78]), np.array([8, 10, 24])
    g = top * (1 - y) + bot * y + np.exp(-((x * 1.1) ** 2 + ((y - 0.3) * 2.4) ** 2)) * np.array([60, 70, 40])
    return Image.fromarray(np.clip(g, 0, 255).astype(np.uint8), "RGB")

def prepare_bg(path, zoom=1.12):
    img = Image.open(path).convert("RGB") if path else placeholder_bg()
    img = ImageOps.fit(img, (int(W * zoom), int(H * zoom)), method=Image.LANCZOS, centering=(0.5, 0.35))
    return img

def overlay(position="bottom"):
    """Dark gradient behind the title/badge and behind the text band, so text stays readable on any picture."""
    yy = np.arange(H, dtype=np.float64)
    head = np.clip(1 - yy / 420, 0, 1) * 0.75
    if position == "top":
        band = np.clip(1 - (yy - 900) / 420, 0, 1) * 0.68          # dark from the top down to ~y=900, fading by 1320
    else:
        band = np.clip((yy - 620) / 520, 0, 1) * 0.86
    foot = np.clip((yy - (H - 260)) / 160, 0, 1) * 0.7               # keep the handle/credit readable
    a = np.maximum(np.maximum(head, band), foot)[:, None]
    alpha = (np.repeat(a, W, axis=1) * 255).astype(np.uint8)
    ov = Image.new("RGBA", (W, H), (6, 4, 12, 0)); ov.putalpha(Image.fromarray(alpha, "L"))
    return ov

def text_position(bg, cfg):
    """'top' or 'bottom': put the text over the calmer half of the picture (less detail), unless config says otherwise."""
    pos = cfg.get("text_position", "auto")
    if pos in ("top", "bottom"): return pos
    g = np.asarray(bg.convert("L").resize((W // 4, H // 4)), dtype=np.float64)
    edges = np.abs(np.diff(g, axis=1)).mean(axis=1)
    top = edges[int(250 / 4):int(1050 / 4)].mean(); bottom = edges[int(950 / 4):int(1730 / 4)].mean()
    return "top" if top < bottom * 0.85 else "bottom"

# ------------------------------------------------------------------ static chrome (title, badge, footer)
CHAPTER_NAMES = ["అర్జునవిషాద యోగము", "సాంఖ్య యోగము", "కర్మ యోగము", "జ్ఞానకర్మసన్న్యాస యోగము", "కర్మసన్న్యాస యోగము",
    "ఆత్మసంయమ యోగము", "జ్ఞానవిజ్ఞాన యోగము", "అక్షరబ్రహ్మ యోగము", "రాజవిద్యారాజగుహ్య యోగము", "విభూతి యోగము",
    "విశ్వరూపసందర్శన యోగము", "భక్తి యోగము", "క్షేత్రక్షేత్రజ్ఞవిభాగ యోగము", "గుణత్రయవిభాగ యోగము",
    "పురుషోత్తమప్రాప్తి యోగము", "దైవాసురసంపద్విభాగ యోగము", "శ్రద్ధాత్రయవిభాగ యోగము", "మోక్షసన్న్యాస యోగము"]

def chrome(reel, cfg):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tf = font(DISPLAY, 66)
    d.text((64, 70), cfg.get("series_title", "శ్రీమద్భగవద్గీత"), font=tf, fill=GOLD)
    sub = font(SANS, 34, 500)
    tb = d.textbbox((64, 70), cfg.get("series_title", "శ్రీమద్భగవద్గీత"), font=tf)
    names = cfg.get("chapter_names") or CHAPTER_NAMES
    name = names[reel["chapter"] - 1] if 0 < reel["chapter"] <= len(names) else ""
    head = f"అధ్యాయం {reel['chapter']}" + (" · " if name else "")
    room = W - 50 - 300 - 68                     # up to the badge
    size = 34
    while size > 24 and tw(head, font(SANS, size, 500)) + tw(name, font(SANS, size, 600)) > room: size -= 1
    y = tb[3] + 22
    d.text((68, y), head, font=font(SANS, size, 500), fill=DIM)
    if name:
        d.text((68 + tw(head, font(SANS, size, 500)), y), name, font=font(SANS, size, 600), fill=GOLD)
    # badge top-right: serial + chapter.verse range
    nf, rf = font(SANS, 62, 800), font(SANS, 38, 600)
    serial, ref = f"#{reel['serial']}", reel["ref"]
    bw = max(tw(serial, nf), tw(ref, rf)) + 64
    x1, y1 = W - 50, 56
    x0, y0 = x1 - bw, y1
    d.rounded_rectangle([x0, y0, x1, y0 + 150], radius=26, fill=(10, 8, 20, 170), outline=GOLD + (230,), width=3)
    d.text((x1 - 32, y0 + 14), serial, font=nf, fill=GOLD, anchor="ra")
    d.text((x1 - 32, y0 + 92), ref, font=rf, fill=CREAM, anchor="ra")
    foot = cfg.get("handle", "")
    if foot:
        d.text((W // 2, H - 118), foot, font=font(SANS, 32, 600), fill=GOLD, anchor="mm")
    cr = cfg.get("credit_line", "")
    if cr:
        d.text((W // 2, H - 72), cr, font=font(SANS, 24, 400), fill=GREY, anchor="mm")
    return shadowed(layer, 5, 0.6)

# ------------------------------------------------------------------ layout of one entry (shloka + anuvadam)
def layout_entry(ch, e, cfg, position="bottom"):
    max_w = W - 130
    if position == "top":
        bottom, top_min = 1180, 250          # below the title/badge, over the sky
    else:
        bottom, top_min = H - 190, 440
    for scale in [1 - 0.05 * i for i in range(10)]:
        size = int(54 * scale)
        while size > 40 and max(tw(l, font(SERIF, size, 700)) for l in e["shloka"]) > max_w:
            size -= 2                                   # prefer one line per half-verse
        sf = font(SERIF, size, 700)
        lines = []
        for l in e["shloka"]:
            lines += wrap(l, sf, max_w)
        mf = font(SANS, int(44 * scale), 400)
        mlines = wrap(e["translation"], mf, max_w)
        s_lh, m_lh = int(sf.size * 1.62), int(mf.size * 1.58)
        head_h = 56 + (50 if e.get("speaker") else 0)
        block = head_h + len(lines) * s_lh + 70 + 52 + len(mlines) * m_lh
        if bottom - block >= top_min:
            break
    y = top_min if position == "top" else bottom - block
    lay = {"sf": sf, "mf": mf, "s_lines": [], "m_lines": [], "y0": y}
    lay["ref_y"] = y; y += 56
    if e.get("speaker"):
        lay["spk_y"] = y; y += 50
    for l in lines:
        lay["s_lines"].append((l, y)); y += s_lh
    lay["div_y"] = y + 26; y += 70
    lay["lab_y"] = y; y += 52
    for l in mlines:
        lay["m_lines"].append((l, y)); y += m_lh
    lay["ref"] = f"శ్లోకం {entry_ref(ch, e)}"
    return lay


# ------------------------------------------------------------------ your own voice (optional, per reel)
import re as _re
AUDIO_EXT = (".m4a", ".mp3", ".wav", ".ogg", ".opus", ".aac", ".amr", ".3gp", ".flac", ".webm", ".mp4")

def voice_number(filename):
    """Reel number from a file name: '1.m4a', '001.mp3', 'reel 1.ogg', 'r1.wav', 'Reel_12 final.m4a'."""
    stem = os.path.splitext(os.path.basename(filename))[0].strip().lower()
    m = _re.match(r"^0*(\d{1,3})(?!\d)", stem) or _re.search(r"(?:reel|r|gita)[\s_\-#.]*0*(\d{1,3})(?!\d)", stem)
    return int(m.group(1)) if m else None

def find_own_voice(serial, cfg):
    d = os.path.join(HERE, cfg.get("own_voice", {}).get("folder", "voice"))
    if not os.path.isdir(d): return None
    hits = [f for f in sorted(os.listdir(d)) if f.lower().endswith(AUDIO_EXT) and voice_number(f) == serial]
    return os.path.join(d, hits[0]) if hits else None

OWN_CLEANUP = ("highpass=f=75,lowpass=f=11000,afftdn=nr=14:nf=-42:tn=1,"
               "silenceremove=start_periods=1:start_threshold=-42dB:start_silence=0.15,areverse,"
               "silenceremove=start_periods=1:start_threshold=-42dB:start_silence=0.25,areverse,"
               "acompressor=threshold=-21dB:ratio=3:attack=8:release=220:makeup=2,loudnorm=I=-16:TP=-1.5:LRA=9")

def load_own_voice(path, cleanup=True):
    af = OWN_CLEANUP if cleanup else "loudnorm=I=-16:TP=-1.5"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-af", af, "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768

def split_by_pauses(x, weights, min_pause=0.3):
    """Cut one recording into len(weights) parts at its longest pauses (falls back to text-length proportions)."""
    k = len(weights)
    if k <= 1: return [(0.0, len(x) / SR)]
    hop = int(0.02 * SR)
    e = np.sqrt(np.convolve(x ** 2, np.ones(hop) / hop, mode="same")[::hop] + 1e-12)
    thr = max(np.percentile(e, 20) * 2.5, np.max(e) * 0.04)
    quiet = e < thr
    runs, i = [], 0
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]: j += 1
            if (j - i) * 0.02 >= min_pause: runs.append(((j - i) * 0.02, (i + j) / 2 * 0.02))
            i = j
        else:
            i += 1
    total = len(x) / SR
    if len(runs) >= k - 1:
        # pick the k-1 longest pauses, but each near where the text lengths say a part should end
        expect = np.cumsum(weights)[:-1] / sum(weights) * total
        cuts, used = [], set()
        for ex in expect:
            best = max((r for r in runs if r[1] not in used), key=lambda r: r[0] - abs(r[1] - ex) / total * 6, default=None)
            if best: cuts.append(best[1]); used.add(best[1])
        cuts = sorted(cuts)
    else:
        cuts = list(np.cumsum(weights)[:-1] / sum(weights) * total)
    edges = [0.0] + cuts + [total]
    return [(edges[i], edges[i + 1] - edges[i]) for i in range(k)]

# ------------------------------------------------------------------ timeline from voice segments
def plan(reel, cfg, use_voice):
    vcfg = cfg.get("voice", {})
    segs = []                     # (kind, entry index, text)
    ch = reel["chapter"]
    if vcfg.get("announce", True):
        f, l = reel["first"], reel["last"]
        rng = sarvam.telugu_number(f) + (f" నుండి {sarvam.telugu_number(l)}" if l != f else "")
        segs.append(("intro", -1, f"శ్రీమద్భగవద్గీత. అధ్యాయం {sarvam.telugu_number(ch)}. శ్లోకాలు {rng}."))
    for i, e in enumerate(reel["entries"]):
        what = vcfg.get("read", "shloka_and_meaning")
        if what in ("shloka_and_meaning", "shloka_only"):
            segs.append(("shloka", i, ((e.get("speaker") or "") + " " + " ".join(e["shloka"])).strip()))
        if what in ("shloka_and_meaning", "meaning_only"):
            segs.append(("meaning", i, e["translation"]))
    timeline, t = [], 1.0
    audio = []
    own = find_own_voice(reel["serial"], cfg) if cfg.get("own_voice", {}).get("enabled", True) else None
    if own:
        print("voice: your recording", os.path.basename(own))
        ocfg = cfg.get("own_voice", {})
        if not ocfg.get("includes_intro", False):
            segs = [sg for sg in segs if sg[0] != "intro"]
        x = load_own_voice(own, ocfg.get("cleanup", True))
        parts = split_by_pauses(x, [len(aksharas(txt)) * (1.6 if kd == "shloka" else 1.0) for kd, _, txt in segs])
        for (kind, i, text), (st, du) in zip(segs, parts):
            timeline.append({"kind": kind, "i": i, "start": t + st, "dur": du})
        audio.append((t, x))
        return timeline, audio, t + len(x) / SR + 1.8
    for kind, i, text in segs:
        if use_voice:
            x = sarvam.speak(text, vcfg)
            dur = len(x) / SR
        else:
            x = None
            dur = len(aksharas(text)) / (6.5 if kind == "shloka" else 11.0)
        timeline.append({"kind": kind, "i": i, "start": t, "dur": dur})
        if x is not None: audio.append((t, x))
        gap = {"intro": 0.7, "shloka": 0.8, "meaning": 1.6}[kind]
        t += dur + gap
    total = t + 1.5
    return timeline, audio, total

# ------------------------------------------------------------------ video
def render_reel(reel, cfg, out_path, use_voice=True, sound=True):
    ch = reel["chapter"]
    timeline, voice, total = plan(reel, cfg, use_voice)
    max_total = cfg.get("max_video_seconds", 175)
    if total > max_total:
        print(f"  ! reel is {total:.0f}s, longer than {max_total}s — YouTube will treat it as a normal video, not a Short")
    bg = prepare_bg(pick_image(reel["serial"], cfg))
    pos = text_position(bg, cfg)
    print("text position:", pos)
    ov, chrome_layer = overlay(pos), chrome(reel, cfg)
    lays = [layout_entry(ch, e, cfg, pos) for e in reel["entries"]]
    # per-entry windows: from its first segment to just before the next entry's
    win = {}
    for seg in timeline:
        if seg["i"] < 0: continue
        s, e_ = seg["start"], seg["start"] + seg["dur"]
        a, b = win.get(seg["i"], (s, e_)); win[seg["i"]] = (min(a, s), max(b, e_))
    keys = sorted(win)
    for k_i, k in enumerate(keys):
        nxt = win[keys[k_i + 1]][0] - 0.45 if k_i + 1 < len(keys) else total
        win[k] = (win[k][0] - 0.25, nxt)
    seg_of = {(s["kind"], s["i"]): s for s in timeline}
    cache = {}

    def typed(key, full, f, fill, frac):
        ak = aksharas(full); k = max(0, min(len(ak), int(math.ceil(len(ak) * frac))))
        ck = (key, k)
        if ck not in cache:
            cache[ck] = line_sprite(full, "".join(ak[:k]), f, fill, W - 60)
        return cache[ck]

    tmp = out_path + ".v.mp4"
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "21",
                             "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp], stdin=subprocess.PIPE)
    BW, BH = bg.size
    nframes = int(total * FPS)
    for fi in range(nframes):
        t = fi / FPS
        z = 1.0 + 0.10 * (t / total)                       # slow zoom in (Ken Burns)
        cw, chh = int(W * 1.12 / z), int(H * 1.12 / z)
        cx = (BW - cw) // 2; cy = int((BH - chh) * 0.4)
        frame = bg.crop((cx, cy, cx + cw, cy + chh)).resize((W, H), Image.BILINEAR).convert("RGBA")
        frame.alpha_composite(ov)
        frame.alpha_composite(with_alpha(chrome_layer, ease(t / 0.8)) if t < 0.8 else chrome_layer)
        d = ImageDraw.Draw(frame)
        for idx in keys:
            a0, a1 = win[idx]
            if not (a0 <= t <= a1 + 0.45): continue
            alpha = min(ease((t - a0) / 0.45), 1 - ease((t - a1) / 0.45)) if t > a1 else ease((t - a0) / 0.45)
            if alpha <= 0: continue
            lay, e = lays[idx], reel["entries"][idx]
            fading = alpha < 0.999
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0)) if fading else frame
            ld = ImageDraw.Draw(layer)
            ld.text((W // 2, lay["ref_y"] + 20), lay["ref"], font=font(SANS, 34, 700), fill=GOLD, anchor="mm")
            if e.get("speaker"):
                ld.text((W // 2, lay["spk_y"] + 20), e["speaker"], font=font(SERIF, 34, 600), fill=GOLD, anchor="mm")
            s = seg_of.get(("shloka", idx))
            sfrac = 1.0 if s is None else (t - s["start"]) / max(0.1, s["dur"] * 0.92)
            total_ak = sum(len(aksharas(l)) for l, _ in lay["s_lines"]) or 1
            shown = sfrac * total_ak
            for li, (l, y) in enumerate(lay["s_lines"]):
                n_ak = len(aksharas(l))
                frac = max(0.0, min(1.0, shown / n_ak)); shown -= n_ak
                if frac > 0:
                    layer.alpha_composite(typed(("s", idx, li), l, lay["sf"], CREAM, frac), (30, y - 12))
            m = seg_of.get(("meaning", idx))
            mstart = m["start"] if m else (s["start"] + s["dur"] + 0.3 if s else a0)
            if t >= mstart - 0.3:
                ma = ease((t - mstart + 0.3) / 0.5)
                ld.line([(W // 2 - 160, lay["div_y"]), (W // 2 + 160, lay["div_y"])], fill=GOLD + (int(200 * ma),), width=2)
                ld.regular_polygon((W // 2, lay["div_y"], 9), 4, rotation=45, fill=GOLD + (int(220 * ma),))
                ld.text((W // 2, lay["lab_y"] + 18), "అనువాదము", font=font(DISPLAY, 42), fill=GOLD + (int(255 * ma),), anchor="mm")
                mfrac = 1.0 if m is None else (t - m["start"]) / max(0.1, m["dur"] * 0.9)
                total_m = sum(len(aksharas(l)) for l, _ in lay["m_lines"]) or 1
                shown = mfrac * total_m
                for li, (l, y) in enumerate(lay["m_lines"]):
                    n_ak = len(aksharas(l))
                    frac = max(0.0, min(1.0, shown / n_ak)); shown -= n_ak
                    if frac > 0:
                        layer.alpha_composite(typed(("m", idx, li), l, lay["mf"], DIM, frac), (30, y - 12))
            # progress dots
            for k in range(len(keys)):
                cxk = W // 2 + (k - (len(keys) - 1) / 2) * 34
                ld.ellipse([cxk - 7, H - 168 - 7, cxk + 7, H - 168 + 7], fill=GOLD + (255,) if k == idx else (255, 255, 255, 90))
            if fading:
                frame.alpha_composite(with_alpha(layer, alpha))
        fade = min(1, (total - t) / 1.0)
        out = frame.convert("RGB")
        if fade < 1: out = Image.blend(Image.new("RGB", (W, H)), out, max(0, fade))
        proc.stdin.write(out.tobytes())
    proc.stdin.close(); proc.wait()

    if sound:
        wav = out_path + ".a.wav"
        mix_audio(wav, total, voice, reel["serial"], cfg)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-i", wav, "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                        "-ar", "44100", "-shortest", "-movflags", "+faststart", out_path], check=True)
        os.remove(wav); os.remove(tmp)
    else:
        os.replace(tmp, out_path)
    return total

def smooth(x, k):
    """Running average over k samples (fast, via cumulative sum)."""
    c = np.cumsum(np.r_[np.full(k // 2, x[0]), x, np.full(k - k // 2, x[-1])])
    return (c[k:] - c[:-k])[:len(x)] / k

def mix_audio(path, total, voice, serial, cfg):
    n = int(total * SR)
    bgm = music.background(total, serial, cfg)[:n]
    bgm = np.pad(bgm, (0, n - len(bgm)))
    gain = np.full(n, cfg.get("music", {}).get("volume", 0.5))
    duck = cfg.get("music", {}).get("under_voice", 0.22)
    for start, x in voice:
        a = int(start * SR); b = min(n, a + len(x))
        gain[a:b] *= duck
    gain = smooth(gain, int(0.5 * SR))
    mix = bgm * gain
    vmix = np.zeros(n)
    for start, x in voice:
        x = x / (np.max(np.abs(x)) or 1) * 0.9
        a = int(start * SR); seg = x[:max(0, n - a)]
        vmix[a:a + len(seg)] += seg
    mix = mix + vmix
    fo = int(1.2 * SR); mix[-fo:] *= np.linspace(1, 0, fo)
    mix[:int(0.3 * SR)] *= np.linspace(0, 1, int(0.3 * SR))
    pcm = (mix / (np.max(np.abs(mix)) or 1) * 0.85 * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())

# ------------------------------------------------------------------ thumbnail + captions
def render_cover(reel, cfg, out_path):
    bg = prepare_bg(pick_image(reel["serial"], cfg), 1.0).convert("RGBA")
    pos = text_position(bg, cfg)
    bg.alpha_composite(overlay(pos)); bg.alpha_composite(chrome(reel, cfg))
    lay = layout_entry(reel["chapter"], reel["entries"][0], cfg, pos)
    d = ImageDraw.Draw(bg)
    for l, y in lay["s_lines"]:
        bg.alpha_composite(line_sprite(l, l, lay["sf"], CREAM, W - 60), (30, y - 12))
    bg.convert("RGB").save(out_path, quality=90)

def captions(reel, cfg, book):
    ch = reel["chapter"]
    names = cfg.get("chapter_names") or CHAPTER_NAMES
    cname = f" ({names[ch - 1]})" if 0 < ch <= len(names) else ""
    parts = [f"🙏 శ్రీమద్భగవద్గీత #{reel['serial']} — అధ్యాయం {ch}{cname}, శ్లోకాలు {reel['ref']}"]
    for e in reel["entries"]:
        block = f"\n{entry_ref(ch, e)}\n" + ("" if not e.get("speaker") else e["speaker"] + "\n") + "\n".join(e["shloka"])
        block += f"\n\nఅనువాదము: {e['translation']}"
        parts.append(block)
    credit = cfg.get("credit_line", "")
    tags = " ".join(cfg.get("hashtags", []))
    body = "\n".join(parts) + (f"\n\n{credit}" if credit else "")
    ig = body + (f"\n\n{tags}" if tags else "")
    if len(ig) > 2200:                       # Instagram limit: keep shlokas, shorten the meanings
        short = [parts[0]] + [f"\n{entry_ref(ch, e)}\n" + "\n".join(e["shloka"]) for e in reel["entries"]]
        ig = ("\n".join(short) + (f"\n\n{credit}" if credit else "") + (f"\n\n{tags}" if tags else ""))[:2200]
    yt_title = f"భగవద్గీత #{reel['serial']} | అధ్యాయం {ch} శ్లోకాలు {reel['ref']} | Bhagavad Gita Telugu #Shorts"[:100]
    return dict(instagram=ig, youtube_title=yt_title, youtube_description=(body + f"\n\n{tags}")[:4900], whatsapp=body)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("serial", type=int)
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--silent", action="store_true")
    a = ap.parse_args()
    cfg, book = load_config(), load_book()
    reels = build_reels(book, cfg.get("shlokas_per_reel", 3))
    reel = reels[a.serial - 1]
    os.makedirs(a.out, exist_ok=True)
    stem = os.path.join(a.out, f"reel-{a.serial:03d}")
    use_voice = not a.no_voice and sarvam.available()
    if find_own_voice(a.serial, cfg): use_voice = True
    if not use_voice and not a.no_voice:
        print("SARVAM_API_KEY not set — rendering without voice (timing estimated)")
    render_cover(reel, cfg, stem + ".jpg")
    dur = render_reel(reel, cfg, stem + ".mp4", use_voice, not a.silent)
    with open(stem + ".txt", "w", encoding="utf-8") as f:
        f.write(captions(reel, cfg, book)["instagram"])
    print(f"reel #{a.serial} ({reel['ref']}): {dur:.1f}s -> {stem}.mp4")

if __name__ == "__main__":
    main()
