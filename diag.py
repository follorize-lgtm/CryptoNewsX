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

print("== A: simple + media_type ==")
r = requests.post(
    BASE,
    auth=oauth,
    files={"media": ("blob", png, "image/png")},
    data={"media_category": "tweet_image", "media_type": "image/png"},
    timeout=60,
)
print("A status:", r.status_code, r.text[:300])

print("== B: chunked initialize/append/finalize ==")
init = requests.post(
    BASE + "/initialize",
    auth=oauth,
    json={"media_category": "tweet_image", "media_type": "image/png", "total_bytes": len(png)},
    timeout=60,
)
print("init:", init.status_code, init.text[:300])
try:
    mid = (init.json().get("data") or {}).get("id") or init.json().get("media_id_string")
    ap = requests.post(
        "%s/%s/append" % (BASE, mid),
        auth=oauth,
        data={"segment_index": 0},
        files={"media": ("blob", png, "application/octet-stream")},
        timeout=60,
    )
    print("append:", ap.status_code, ap.text[:300])
    fin = requests.post("%s/%s/finalize" % (BASE, mid), auth=oauth, timeout=60)
    print("finalize:", fin.status_code, fin.text[:300])
except Exception as exc:
    print("chunked error:", exc)
