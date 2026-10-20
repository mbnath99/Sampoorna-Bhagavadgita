"""Rebuild data/gita.json from the cleaned OCR + hand corrections (data/corrections.json) + proofread marks."""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cleaned = json.load(open(sys.argv[1], encoding="utf-8"))
corr = json.load(open(os.path.join(HERE, "data", "corrections.json"), encoding="utf-8"))
proof = json.load(open(os.path.join(HERE, "data", "proofread.json"), encoding="utf-8")) if os.path.exists(os.path.join(HERE, "data", "proofread.json")) else {"chapters": []}
chs, missing = {}, []
for x in cleaned:
    key = f"{x['chapter']}.{x['verses'][0]}"
    t = x["translation"]
    for old, new in corr.get(key, []):
        if old not in t: missing.append((key, old))
        t = t.replace(old, new)
    if key in corr.get("_replace", {}): t = corr["_replace"][key]
    chs.setdefault(x["chapter"], []).append({"verses": x["verses"], "speaker": x["speaker"], "shloka": x["shloka"],
                                            "translation": t, "proofread": x["chapter"] in proof["chapters"], "page": x["page"]})
book = {"title": "శ్రీమద్భగవద్గీత",
        "source": "Sanskrit: public domain (github.com/gita/gita). Telugu translation: Bhagavad-gita As It Is, © The Bhaktivedanta Book Trust International",
        "chapters": [{"n": c, "entries": chs[c]} for c in sorted(chs)]}
json.dump(book, open(os.path.join(HERE, "data", "gita.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("built; corrections not found:", missing if missing else "none")
