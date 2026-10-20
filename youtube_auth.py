#!/usr/bin/env python3
"""
One-time YouTube sign-in. Run this on YOUR computer (it opens a browser):

    pip install google-auth-oauthlib
    python youtube_auth.py path/to/client_secret.json

Sign in with the Google account that owns your YouTube channel and allow "Manage your YouTube videos".
It prints three values — paste them into GitHub secrets YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN.
"""
import json, sys
from google_auth_oauthlib.flow import InstalledAppFlow

if len(sys.argv) != 2:
    sys.exit(__doc__)
flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], scopes=["https://www.googleapis.com/auth/youtube.upload"])
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
info = json.load(open(sys.argv[1]))
client = info.get("installed") or info.get("web")
print("\nAdd these as GitHub repository secrets:\n")
print("YT_CLIENT_ID     =", client["client_id"])
print("YT_CLIENT_SECRET =", client["client_secret"])
print("YT_REFRESH_TOKEN =", creds.refresh_token)
