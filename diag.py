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

init = requests.post(
    BASE,
    auth=oauth,
    data={"command": "INIT", "total_bytes": len(png), "media_type": "image/png", "media_category": "tweet_image"},
    timeout=60,
)
print("INIT:", init.status_code, init.text[:300])
j = init.json()
mid = (j.get("data") or j).get("id") or j.get("media_id_string") or j.get("media_id")
print("MEDIA_ID:", mid)

ap = requests.post(
    BASE,
    auth=oauth,
    data={"command": "APPEND", "media_id": mid, "segment_index": 0},
    files={"media": ("blob", png, "application/octet-stream")},
    timeout=60,
)
print("APPEND:", ap.status_code, ap.text[:200])

fin = requests.post(
    BASE,
    auth=oauth,
    data={"command": "FINALIZE", "media_id": mid},
    timeout=60,
)
print("FINALIZE:", fin.status_code, fin.text[:300])
