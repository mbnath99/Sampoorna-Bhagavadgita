"""Stage 2: walk the classified lines in page order, find each entry (verse number -> shloka -> red translation),
OCR the verse number and the translation, write entries_raw.json.

  python assemble.py linesdir out.json [workers]
"""
import json, os, re, subprocess, sys, glob
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image

LD, OUT = sys.argv[1], sys.argv[2]
WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 2
TESS = os.environ.get("TESSDATA", "")
env = {**os.environ, "OMP_THREAD_LIMIT": "1"}

pages = [json.load(open(f)) for f in sorted(glob.glob(os.path.join(LD, "p*.json")))]
seq = [dict(l, page=p["page"]) for p in pages for l in p["lines"]]

entries, i = [], 0
while i < len(seq):
    l = seq[i]
    if l["cls"] != "blue":
        i += 1; continue
    blue = []
    while i < len(seq) and seq[i]["cls"] == "blue":
        blue.append(seq[i]); i += 1
    red = []
    j = i
    while j < len(seq) and seq[j]["cls"] in ("red",):
        red.append(seq[j]); j += 1
    i = j
    if not red:
        continue                          # blue text that isn't a verse block (e.g. chapter titles)
    if red and (red[0]["x1"] - red[0]["x0"]) < 320:
        red = red[1:]                     # the 'అనువాదము' heading
    entries.append({"page": blue[0]["page"], "num_crop": blue[0].get("crop"), "skt_crops": [b["crop"] for b in blue[1:] if b.get("crop")],
                    "red_crops": [r["crop"] for r in red if r.get("crop")]})

def ocr(img_path, lang, psm, extra=()):
    r = subprocess.run(["tesseract", img_path, "-", "--psm", str(psm), "-l", lang, *(["--tessdata-dir", TESS] if TESS else []), *extra],
                       capture_output=True, text=True, env=env)
    return r.stdout.strip()

def stack(crops, path):
    ims = [Image.open(os.path.join(LD, c)) for c in crops]
    w = max(im.width for im in ims); h = sum(im.height + 12 for im in ims)
    out = Image.new("L", (w + 40, h + 40), 255)
    y = 20
    for im in ims:
        out.paste(im, (20, y)); y += im.height + 12
    out.save(path)

def work(k):
    e = entries[k]
    num = ""
    if e["num_crop"]:
        num = ocr(os.path.join(LD, e["num_crop"]), "eng", 7, ("-c", "tessedit_char_whitelist=0123456789.- "))
    text = ""
    if e["red_crops"]:
        p = os.path.join(LD, f"tr_{k:04d}.png")
        stack(e["red_crops"], p)
        e["tr_img"] = os.path.basename(p)
        text = ocr(p, "tel", 6)
    skt = ""
    if e["skt_crops"]:
        p2 = os.path.join(LD, f"sk_{k:04d}.png")
        stack(e["skt_crops"], p2)
        skt = ocr(p2, "tel", 6)
    e["num_raw"], e["text_raw"], e["skt_raw"] = num, text, skt
    return k

with ThreadPoolExecutor(WORKERS) as ex:
    for n, k in enumerate(ex.map(work, range(len(entries)))):
        if n % 50 == 0: print(n, "/", len(entries), flush=True)
json.dump(entries, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("entries:", len(entries))
