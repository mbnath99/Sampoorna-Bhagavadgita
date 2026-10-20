"""Stage 1: for each PDF page, find text lines and classify them by colour; save crops of blue + red lines.

  python extract_lines.py g.pdf outdir first_page last_page
"""
import json, os, subprocess, sys, tempfile
import numpy as np
from PIL import Image

pdf, out, p0, p1 = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
os.makedirs(out, exist_ok=True)
for p in range(p0, p1 + 1):
    jf = os.path.join(out, f"p{p:03d}.json")
    if os.path.exists(jf):
        continue
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", "200", "-f", str(p), "-l", str(p), "-png", pdf, f"{td}/x"], check=True)
        f = [x for x in os.listdir(td) if x.endswith(".png")][0]
        im = np.array(Image.open(os.path.join(td, f)).convert("RGB")).astype(np.int16)
    R, G, B = im[..., 0], im[..., 1], im[..., 2]
    ink = (R + G + B) < 560
    red = (R > 120) & (G < 100) & (B < 100) & (R - G > 70)
    blue = (B - R > 35) & (R < 110) & (G < 130) & ink
    green = (G - R > 8) & (G - B > 25) & (G < 150) & ink
    H = im.shape[0]
    rows = ink.sum(1) > 2
    lines, y = [], 0
    top, bottom = int(H * 0.07), int(H * 0.965)          # skip running header/footer
    while y < H:
        if rows[y] and top <= y <= bottom:
            y0 = y
            while y < H and rows[y]: y += 1
            y1 = y
            if y1 - y0 >= 8:
                sl = slice(y0, y1)
                n_ink = ink[sl].sum(); n_r, n_b, n_g = red[sl].sum(), blue[sl].sum(), green[sl].sum()
                cls = "black"
                if n_r > 0.35 * n_ink: cls = "red"
                elif n_b > 0.35 * n_ink: cls = "blue"
                elif n_g > 0.35 * n_ink: cls = "green"
                xs = np.where(ink[sl].any(0))[0]
                lines.append({"y0": int(y0), "y1": int(y1), "cls": cls, "x0": int(xs.min()), "x1": int(xs.max())})
        y += 1
    for k, ln in enumerate(lines):
        if ln["cls"] in ("red", "blue"):
            m = red if ln["cls"] == "red" else blue
            sl = slice(max(0, ln["y0"] - 6), ln["y1"] + 6)
            crop = np.where(m[sl], 0, 255).astype(np.uint8)
            name = f"p{p:03d}_{k:02d}_{ln['cls']}.png"
            Image.fromarray(crop).save(os.path.join(out, name))
            ln["crop"] = name
    json.dump({"page": p, "lines": lines}, open(jf, "w"))
    print(p, len(lines), flush=True)
