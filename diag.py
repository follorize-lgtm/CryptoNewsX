import os

import requests
from dotenv import load_dotenv
from requests_oauthlib import OAuth1

load_dotenv()

oauth = OAuth1(
    os.environ["X_API_KEY"],
    os.environ["X_API_SECRET"],
    os.environ["X_ACCESS_TOKEN"],
    os.environ["X_ACCESS_SECRET"],
)

png = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d4944415478da6364f80f00010101001b2d5c0e0000"
    "000049454e44ae426082"
)

BASE = "https://api.x.com/2/media/upload"
body = {"media_category": "tweet_image", "media_type": "image/png", "total_bytes": len(png)}


def get_id(resp):
    try:
        j = resp.json()
    except Exception:
        return None
    d = j.get("data") or j
    return d.get("id") or d.get("media_id_string") or d.get("media_id")


a = requests.post(BASE, auth=oauth, json=body, timeout=60)
print("INIT base:", a.status_code, a.text[:250])
mid = get_id(a)
if not mid:
    b = requests.post(BASE + "/initialize", auth=oauth, json=body, timeout=60)
    print("INIT /initialize:", b.status_code, b.text[:250])
    mid = get_id(b)
print("MEDIA_ID:", mid)

if mid:
    ap = requests.post(
        "%s/%s/append" % (BASE, mid),
        auth=oauth,
        data={"segment_index": 0},
        files={"media": ("blob", png, "application/octet-stream")},
        timeout=60,
    )
    print("APPEND:", ap.status_code, ap.text[:200])
    fin = requests.post("%s/%s/finalize" % (BASE, mid), auth=oauth, timeout=60)
    print("FINALIZE:", fin.status_code, fin.text[:250])
