#!/usr/bin/env python3
"""
Publishes today's post (built by daily.py into ./site) to Instagram, YouTube and,
optionally, a Facebook Page; sends an optional phone reminder (ntfy) for the
WhatsApp/Facebook-profile share. Records every result in state/posted_log.csv so a
re-run on the same day never double-posts.

Secrets come from environment variables (GitHub Actions secrets):
  IG_USER_ID, IG_ACCESS_TOKEN                      Instagram (Instagram API with Instagram Login)
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN YouTube
  FB_PAGE_ID, FB_PAGE_TOKEN                        optional Facebook Page
  GH_PAT                                           optional: lets the job save a refreshed IG token

  python publish.py --site-url https://<you>.github.io/<repo>/  [--only instagram,youtube] [--dry-run]
"""
import argparse, csv, datetime as dt, json, os, subprocess, sys, time
import requests
import render

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "state", "posted_log.csv")
IG_HOST = "https://graph.instagram.com"
FB_HOST = "https://graph.facebook.com"

# ------------------------------------------------------------------ log
def already_posted(date, platform):
    if not os.path.exists(LOG): return False
    with open(LOG, encoding="utf-8") as f:
        return any(r["date"] == date and r["platform"] == platform and r["status"] == "ok" for r in csv.DictReader(f))

def log(date, n, platform, status, detail=""):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(["date", "padyam", "platform", "status", "detail", "logged_at_utc"])
        w.writerow([date, n, platform, status, detail, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")])
    print(f"[{platform}] {status} {detail}")

def wait_public(url, tries=30):
    for _ in range(tries):
        try:
            r = requests.head(url, timeout=20, allow_redirects=True)
            if r.status_code == 200: return True
        except requests.RequestException:
            pass
        time.sleep(10)
    return False

# ------------------------------------------------------------------ Instagram
def ig_refresh_token(cfg):
    """Long-lived Instagram tokens last 60 days; refreshing extends them. Saves to GitHub secret if GH_PAT set."""
    token = os.environ["IG_ACCESS_TOKEN"]
    r = requests.get(f"{IG_HOST}/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=60)
    if r.status_code != 200:
        print("[instagram] token refresh skipped:", r.text[:200]); return token
    new = r.json().get("access_token", token)
    days = int(r.json().get("expires_in", 0)) // 86400
    print(f"[instagram] token refreshed, valid {days} more days")
    if new != token and os.environ.get("GH_PAT") and os.environ.get("GITHUB_REPOSITORY"):
        subprocess.run(["gh", "secret", "set", "IG_ACCESS_TOKEN", "--body", new, "--repo", os.environ["GITHUB_REPOSITORY"]],
                       env={**os.environ, "GH_TOKEN": os.environ["GH_PAT"]}, check=False)
    return new

def ig_publish(kind, media_url, caption, token, user_id, ver):
    params = {"access_token": token, "video_url": media_url}
    if kind == "reel":
        params.update(media_type="REELS", caption=caption, share_to_feed="true")
    else:
        params.update(media_type="STORIES")
    r = requests.post(f"{IG_HOST}/{ver}/{user_id}/media", data=params, timeout=120)
    r.raise_for_status()
    cid = r.json()["id"]
    for _ in range(60):  # video processing usually takes 20-90 s
        s = requests.get(f"{IG_HOST}/{ver}/{cid}", params={"fields": "status_code,status", "access_token": token}, timeout=60).json()
        if s.get("status_code") == "FINISHED": break
        if s.get("status_code") in ("ERROR", "EXPIRED"): raise RuntimeError(f"Instagram processing failed: {s}")
        time.sleep(10)
    else:
        raise RuntimeError("Instagram processing timed out")
    r = requests.post(f"{IG_HOST}/{ver}/{user_id}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=120)
    r.raise_for_status()
    return r.json()["id"]

# ------------------------------------------------------------------ YouTube
def yt_upload(path, title, description, privacy):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"], token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"],
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {"snippet": {"title": title, "description": description, "categoryId": "27",
                        "tags": ["Bhagavad Gita", "భగవద్గీత", "Gita Telugu", "Krishna", "Telugu", "Shorts"],
                        "defaultLanguage": "te", "defaultAudioLanguage": "te"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(path, mimetype="video/mp4", resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]

# ------------------------------------------------------------------ Facebook Page (optional)
def fb_page_video(video_url, description, page_id, token, ver):
    r = requests.post(f"{FB_HOST}/{ver}/{page_id}/videos",
                      data={"file_url": video_url, "description": description, "access_token": token}, timeout=300)
    r.raise_for_status()
    return r.json().get("id")

# ------------------------------------------------------------------ friendly reporting
REQUIRED = {
    "instagram": ["IG_USER_ID", "IG_ACCESS_TOKEN"],
    "instagram_story": ["IG_USER_ID", "IG_ACCESS_TOKEN"],
    "youtube": ["YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"],
    "facebook_page": ["FB_PAGE_ID", "FB_PAGE_TOKEN"],
    "ntfy": [],
}
NAMES = {"instagram": "Instagram Reel", "instagram_story": "Instagram Story", "youtube": "YouTube Short",
         "facebook_page": "Facebook Page", "ntfy": "Phone reminder"}


def explain(platform, detail):
    """Turn an API error into a plain-English cause + fix."""
    d = detail.lower()
    if platform.startswith("instagram") or platform == "facebook_page":
        if "error validating access token" in d or "session has expired" in d or '"code":190' in d or "code: 190" in d:
            return "The Instagram access token has expired or is wrong. Generate a new one (guide Step 3, item 5) and update the IG_ACCESS_TOKEN secret."
        if "cannot parse access token" in d or "invalid oauth" in d:
            return "IG_ACCESS_TOKEN doesn't look like a valid token. Copy it again carefully (no spaces or quotes) into the secret."
        if "does not exist" in d or "unsupported get request" in d or "unsupported post request" in d:
            return "IG_USER_ID is wrong. Use the Instagram user ID shown next to your account on the 'API setup with Instagram login' page."
        if "media" in d and ("fetch" in d or "download" in d or "uri" in d or "2207" in d):
            return "Instagram couldn't download the video from your GitHub Pages site. Check the repository is Public and Settings → Pages → Source is 'GitHub Actions', then rerun."
        if "permission" in d or "(#10)" in d or "(#200)" in d:
            return "The Meta app is missing publish permission. In the Meta app, make sure your Instagram account was added under 'API setup with Instagram login' and the token was generated there."
        if "application request limit" in d or "(#4)" in d or "rate" in d:
            return "Instagram's posting limit was hit. It resets within 24 hours; nothing to change."
        if "processing failed" in d or "timed out" in d:
            return "Instagram couldn't process the video. Rerun once; if it repeats, send me the error text."
    if platform == "youtube":
        if "invalid_grant" in d:
            return "YouTube sign-in was cancelled. Usually the OAuth consent screen was left in 'Testing' (expires after 7 days). Publish it 'In production' (guide Step 4, item 4) and run youtube_auth.py again."
        if "invalid_client" in d or "unauthorized_client" in d:
            return "YT_CLIENT_ID or YT_CLIENT_SECRET is wrong. Copy both again from the youtube_auth.py output."
        if "quotaexceeded" in d or "quota" in d:
            return "YouTube's daily upload quota is used up. It resets at midnight Pacific time; nothing to change."
        if "uploadlimitexceeded" in d:
            return "Your channel hit YouTube's upload limit for today. Verify the channel at youtube.com/verify to raise it."
        if "youtubesignuprequired" in d or "channel" in d and "not found" in d:
            return "That Google account has no YouTube channel. Sign in with the account that owns the channel when running youtube_auth.py."
        if "accessnotconfigured" in d or "has not been used in project" in d:
            return "The YouTube Data API v3 isn't enabled in your Google Cloud project (guide Step 4, item 2)."
    return "Unexpected error — send me the text in the 'Details' column."

def annotate(level, title, msg):
    """Shows up in the run's Annotations box on GitHub."""
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{level} title={title}::{msg}")

def summary(rows, n, video_url, base):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    lines = [f"## Reel #{n}", "", f"Share page: {base}  ·  [video]({video_url})", "",
             "| Platform | Result | What to do | Details |", "| --- | --- | --- | --- |"]
    for platform, status, fix, detail in rows:
        icon = {"ok": "✅ posted", "skipped": "⏭ skipped", "error": "❌ failed", "done": "✅ already posted"}.get(status, status)
        lines.append(f"| {NAMES.get(platform, platform)} | {icon} | {fix} | {detail.replace('|', '/')[:200]} |")
    text = "\n".join(lines) + "\n"
    print(text)
    if path:
        with open(path, "a", encoding="utf-8") as f: f.write(text)

# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site-url", required=True, help="public base URL of the GitHub Pages site")
    ap.add_argument("--site", default=os.path.join(HERE, "site"))
    ap.add_argument("--only", help="comma list: instagram,instagram_story,youtube,facebook_page,ntfy")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = render.load_config()
    with open(os.path.join(a.site, "today.json"), encoding="utf-8") as f:
        today = json.load(f)
    base = a.site_url.rstrip("/") + "/"
    video_url, image_url = base + today["video"], base + today["image"]
    video_path = os.path.join(a.site, today["video"])
    date, n = today["date"], today["n"]
    post_to = cfg.get("post_to", {})
    if not cfg.get("bbt_permission_confirmed") and not a.dry_run:
        msg = ("Posting is paused: the Telugu translation belongs to the Bhaktivedanta Book Trust. After you have their "
               "written permission, set \"bbt_permission_confirmed\": true in config.json. (Video and share page were built.)")
        annotate("warning", "Posting paused", msg)
        summary([(p, "skipped", msg, "") for p in post_to if post_to.get(p)], n, video_url, base)
        sys.exit(0)
    wanted = [p for p in ("instagram", "instagram_story", "youtube", "facebook_page") if post_to.get(p)]
    if cfg.get("ntfy_topic"): wanted.append("ntfy")
    if a.only: wanted = [p for p in wanted if p in a.only.split(",")]
    ver = cfg.get("graph_api_version", "v23.0")
    failures = 0
    rows = []

    # platforms whose secrets aren't added yet are skipped, not failed
    ready = []
    for p in wanted:
        missing = [k for k in REQUIRED.get(p, []) if not os.environ.get(k, "").strip()]
        if missing:
            fix = f"Not set up yet: add secret(s) {', '.join(missing)} (Settings → Secrets and variables → Actions), or turn it off in config.json → post_to."
            rows.append((p, "skipped", fix, ""))
            annotate("warning", f"{NAMES[p]} skipped", fix)
        else:
            ready.append(p)

    if any(p in ready for p in ("instagram", "instagram_story", "facebook_page")) and not a.dry_run:
        if not wait_public(video_url):
            fix = ("The video isn't reachable on your GitHub Pages site, so Instagram can't fetch it. Make the repository "
                   "Public and set Settings → Pages → Source to 'GitHub Actions', then rerun.")
            annotate("error", "Share page not public", fix)
            rows.append(("instagram", "error", fix, video_url)); summary(rows, n, video_url, base); sys.exit(1)

    for p in ready:
        if already_posted(date, p):
            rows.append((p, "done", "Nothing — posted earlier today.", "")); continue
        if a.dry_run:
            rows.append((p, "skipped", "Dry run — nothing posted.", video_url)); continue
        try:
            if p in ("instagram", "instagram_story"):
                token = ig_refresh_token(cfg) if p == "instagram" and dt.date.today().weekday() == 0 else os.environ["IG_ACCESS_TOKEN"]
                mid = ig_publish("reel" if p == "instagram" else "story", video_url, today["instagram"],
                                 token, os.environ["IG_USER_ID"], ver)
                log(date, n, p, "ok", f"media {mid}"); rows.append((p, "ok", "", f"media {mid}"))
            elif p == "youtube":
                vid = yt_upload(video_path, today["youtube_title"], today["youtube_description"],
                                cfg.get("youtube_privacy", "public"))
                log(date, n, p, "ok", f"https://youtube.com/shorts/{vid}"); rows.append((p, "ok", "", f"https://youtube.com/shorts/{vid}"))
            elif p == "facebook_page":
                vid = fb_page_video(video_url, today["whatsapp"], os.environ["FB_PAGE_ID"], os.environ["FB_PAGE_TOKEN"], ver)
                log(date, n, p, "ok", f"video {vid}"); rows.append((p, "ok", "", f"video {vid}"))
            elif p == "ntfy":
                requests.post(f"https://ntfy.sh/{cfg['ntfy_topic']}", timeout=30,
                              data=f"భగవద్గీత #{n} సిద్ధం".encode("utf-8"),
                              headers={"Title": "Today's Gita reel", "Click": base, "Tags": "pray"})
                log(date, n, p, "ok", base); rows.append((p, "ok", "", base))
        except Exception as e:
            failures += 1
            detail = getattr(getattr(e, "response", None), "text", "") or str(e)
            detail = detail[:300].replace("\n", " ")
            log(date, n, p, "error", detail)
            fix = explain(p, detail)
            rows.append((p, "error", fix, detail))
            annotate("error", f"{NAMES.get(p, p)} failed", fix + "  Details: " + detail[:150])
    summary(rows, n, video_url, base)
    sys.exit(1 if failures else 0)

if __name__ == "__main__":
    main()
