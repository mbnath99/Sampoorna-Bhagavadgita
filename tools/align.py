"""Stage 3: match each BBT entry (OCR'd Sanskrit) to consecutive verses of the public-domain Sanskrit source.

  python align.py entries_raw.json verse.json aligned.json
"""
import json, re, sys
from difflib import SequenceMatcher
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from deva2tel import split_verse

ent = json.load(open(sys.argv[1], encoding="utf-8"))
src = json.load(open(sys.argv[2], encoding="utf-8"))

def norm(t):
    t = t.replace(":", "ః")
    t = re.sub(r"[^ఀ-౿]", "", t)          # Telugu letters only
    t = re.sub(r"[ఙఞణనమ]్(?=[కఖగఘచఛజఝటఠడఢతథదధపఫబభ])", "ం", t)
    return t

by_ch = {}
for v in src:
    sp, lines = split_verse(v["text"])
    items = by_ch.setdefault(v["chapter_number"], [])
    for l in lines: items.append({"kind": "line", "text": l, "norm": norm(l), "v": v["verse_number"]})

def parse_num(s):
    m = re.match(r"\s*(\d{1,2})\s*\.\s*(\d{1,2})(?:\s*-\s*(\d{1,2}))?", s or "")
    if not m: return None
    c, a, b = int(m.group(1)), int(m.group(2)), int(m.group(3) or m.group(2))
    return c, a, b

def sim(a, b):
    if not a or not b: return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()

def take(items, s, h):
    """From position s, take h half-verse lines (plus any speaker labels met on the way). Returns (end, chunk)."""
    got, j = 0, s
    while j < len(items) and got < h:
        if items[j]["kind"] == "line": got += 1
        j += 1
    return (j, items[s:j]) if got == h else (None, None)

SPEAKERS = ["ధృతరాష్ట్ర ఉవాచ", "సంజయ ఉవాచ", "అర్జున ఉవాచ", "శ్రీభగవానువాచ"]
def find_nums(raw):
    """All plausible (c, a, b) readings of an OCR'd verse number like '1.32 -35', '- 5.7', '43', '934'."""
    raw = raw or ""
    out = []
    for m in re.finditer(r"(\d{1,2})\s*\.\s*(\d{1,2})(?:\s*-\s*(\d{1,2}))?", raw):
        out.append((int(m.group(1)), int(m.group(2)), int(m.group(3) or m.group(2))))
    digits = re.sub(r"\D", "", raw)
    for cut in (1, 2):                                   # '43' -> 4.3 ; '934' -> 9.34 ; '1012' -> 10.12
        if len(digits) > cut:
            out.append((int(digits[:cut]), int(digits[cut:cut + 2]), int(digits[cut:cut + 2])))
    return out

out, report, ptr = [], [], {}
prev = (1, 0)                                            # (chapter, last verse) of the previous entry
for k, e in enumerate(ent):
    cands = find_nums(e.get("num_raw"))
    nxt_same, nxt_new = (prev[0], prev[1] + 1), (prev[0] + 1, 1)
    num = None
    for c_, a_, b_ in cands:
        if (c_, a_) in (nxt_same, nxt_new) and b_ >= a_ and b_ - a_ <= 6:
            num = (c_, a_, b_); break
    if num is None:
        # unreadable: assume the next verse; a chapter change shows up as a far better Sanskrit match
        o = norm(e.get("skt_raw", ""))
        def first_sim(ch):
            items = by_ch.get(ch, []); s0 = ptr.get(ch, 0)
            return sim(o, "".join(x["norm"] for x in items[s0:s0 + 2])) if items[s0:s0 + 2] else 0
        c_, a_ = nxt_new if first_sim(nxt_new[0]) > first_sim(nxt_same[0]) + 0.15 else nxt_same
        num = (c_, a_, a_)
        report.append(f"entry {k} page {e['page']}: verse number {e.get('num_raw')!r} unreadable -> assumed {c_}.{a_}")
    c, a, b = num
    prev = (c, b)
    if c not in by_ch:
        report.append(f"entry {k}: chapter {c}?"); continue
    items = by_ch[c]; s = ptr.get(c, 0)
    o = norm(e.get("skt_raw", ""))
    n = b - a + 1
    best = None
    for h in range(max(1, 2 * n - 2), 2 * n + 3):
        end, chunk = take(items, s, h)
        if chunk is None: continue
        score = sim(o, "".join(x["norm"] for x in chunk)) - 0.015 * abs(h - 2 * n)
        if best is None or score > best[0]: best = (score, end, chunk, h)
    if best is None:
        report.append(f"{c}.{a}-{b}: ran past the end of chapter {c}"); continue
    score, end, chunk, h = best
    ptr[c] = end
    body = chunk
    lines = [re.sub(r"\s*[।॥]\s*$", "", x["text"]) for x in body]
    lines = [l + (" ॥" if (i % 2 == 1 or i == len(lines) - 1) else " ।") for i, l in enumerate(lines)]
    lines = [l.replace("ృ఼", "ౄ").replace("఼", "") for l in lines]
    # speaker labels come from the book's own layout: an OCR line containing ఉవాచ
    speaker, inserts, nline = None, [], 0
    for ol in [x for x in e.get("skt_raw", "").split("\n") if x.strip()]:
        if "వాచ" in ol and len(norm(ol)) < 22:
            who = max(SPEAKERS, key=lambda sp_: sim(norm(ol), norm(sp_)))
            if nline == 0 and speaker is None: speaker = who
            else: inserts.append((nline, who))
        else:
            nline += 1
    for pos, who in reversed(inserts):
        pos = min(pos, len(lines))
        lines.insert(pos, who)
    out.append({"chapter": c, "verses": list(range(a, b + 1)), "src_lines": [x["v"] for x in body],
                "speaker": speaker, "shloka": lines, "translation_raw": e.get("text_raw", ""), "page": e["page"],
                "tr_img": e.get("tr_img"), "match": round(score, 2), "half_lines": h})
    if h != 2 * n: report.append(f"{c}.{a}-{b}: {h} half-lines (normal is {2 * n}) — match {score:.2f}")
    if score < 0.6: report.append(f"{c}.{a}-{b}: weak Sanskrit match {score:.2f} (page {e['page']})")
for c, items in by_ch.items():
    if ptr.get(c, 0) != len(items):
        report.append(f"chapter {c}: used {ptr.get(c, 0)} of {len(items)} source items")
json.dump(out, open(sys.argv[3], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(out), "entries aligned"); print("\n".join(report) if report else "no problems")
