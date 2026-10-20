#!/usr/bin/env python3
"""
Builds today's Gita reel: reel #1 on start_date, #2 the next day, and so on.

  python daily.py                     # today's reel (India time)
  python daily.py --serial 5          # a specific reel
  python daily.py --schedule 10       # list the next 10 days
  python daily.py --list              # every reel: serial, chapter.verses
"""
import argparse, datetime as dt, json, os, shutil
from zoneinfo import ZoneInfo
import render, sarvam

HERE = os.path.dirname(os.path.abspath(__file__))
IST = ZoneInfo("Asia/Kolkata")

def reel_for(date, cfg, reels):
    idx = (date - dt.date.fromisoformat(cfg["start_date"])).days
    if idx < 0: return None, f"start_date {cfg['start_date']} is in the future"
    if idx >= len(reels):
        if cfg.get("after_last", "stop") == "stop": return None, "all reels have been posted"
        idx %= len(reels)
    return reels[idx], None

def out(name, value):
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as f: f.write(f"{name}={value}\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date"); ap.add_argument("--serial", type=int)
    ap.add_argument("--schedule", type=int); ap.add_argument("--list", action="store_true")
    ap.add_argument("--site", default=os.path.join(HERE, "site"))
    a = ap.parse_args()
    cfg, book = render.load_config(), render.load_book()
    reels = render.build_reels(book, cfg.get("shlokas_per_reel", 3))
    today = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(IST).date()
    if a.list:
        for r in reels: print(f"#{r['serial']:3d}  {r['ref']:10s} ({len(r['entries'])} entries)")
        print(len(reels), "reels"); return
    if a.schedule:
        for i in range(a.schedule):
            d = today + dt.timedelta(days=i); r, why = reel_for(d, cfg, reels)
            img = os.path.basename(render.pick_image(r["serial"], cfg) or "no image") if r else ""
            vo = ("your voice" if render.find_own_voice(r["serial"], cfg) else "Sarvam") if r else ""
            print(d, "->", f"#{r['serial']} {r['ref']}  [{img}] [{vo}]" if r else why)
        return
    if a.serial:
        reel = reels[a.serial - 1]
    else:
        reel, why = reel_for(today, cfg, reels)
        if reel is None:
            print("Nothing to post:", why); out("post", "false"); return
    if cfg.get("require_proofread", True) and not all(e.get("proofread") for e in reel["entries"]):
        msg = (f"Reel #{reel['serial']} ({reel['ref']}) is from a chapter whose translations are not proofread yet, so it was "
               "not posted. Proofread it (data/corrections.json), then add the chapter to data/proofread.json.")
        print(msg)
        if os.environ.get("GITHUB_ACTIONS"): print(f"::warning title=Not proofread yet::{msg}")
        out("post", "false"); return
    own = render.find_own_voice(reel["serial"], cfg)
    use_voice = sarvam.available() or bool(own)
    print("voice:", f"your recording {os.path.basename(own)}" if own else ("Sarvam AI" if use_voice else "none"))
    if not use_voice:
        if os.environ.get("GITHUB_ACTIONS") and cfg.get("voice", {}).get("required", True):
            raise SystemExit("SARVAM_API_KEY secret is missing — add it (Settings → Secrets → Actions) or set voice.required to false.")
        print("SARVAM_API_KEY not set: building without voice")
    site = a.site
    if os.path.exists(site): shutil.rmtree(site)
    os.makedirs(os.path.join(site, "media"))
    stem = f"{today.isoformat()}-gita-{reel['serial']:03d}"
    video, image = os.path.join(site, "media", stem + ".mp4"), os.path.join(site, "media", stem + ".jpg")
    print(f"{today}: reel #{reel['serial']} — {reel['ref']}")
    render.render_cover(reel, cfg, image)
    dur = render.render_reel(reel, cfg, video, use_voice, True)
    caps = render.captions(reel, cfg, book)
    e0 = reel["entries"][0]
    today_json = dict(date=today.isoformat(), n=reel["serial"], title="శ్రీమద్భగవద్గీత", handle=cfg.get("handle", ""),
                      padyam=[f"#{reel['serial']} · {reel['ref']}"] + e0["shloka"], bhavam=e0["translation"],
                      video=f"media/{stem}.mp4", image=f"media/{stem}.jpg", duration=round(dur, 1), **caps)
    json.dump(today_json, open(os.path.join(site, "today.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    shutil.copy(os.path.join(HERE, "site_template", "index.html"), os.path.join(site, "index.html"))
    os.makedirs(os.path.join(site, "fonts"))
    for fn in ("NotoSansTelugu.ttf", "Suravaram.ttf"):
        shutil.copy(os.path.join(HERE, "fonts", fn), os.path.join(site, "fonts", fn))
    open(os.path.join(site, ".nojekyll"), "w").close()
    print(f"built site/ ({dur:.1f}s video)")
    out("post", "true"); out("n", reel["serial"]); out("date", today.isoformat())

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=Building today's reel failed::{type(e).__name__}: {str(e)[:300]}")
        raise
