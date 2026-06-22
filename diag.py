import os
import struct
import zlib

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


def make_png(w, h):
    def chunk(typ, data):
        body = typ + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x29\x86\xc7" * w for _ in range(h))
    idat = zlib.compress(raw, 9)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


png = make_png(200, 200)
BASE = "https://api.x.com/2/media/upload"
body = {"media_category": "tweet_image", "media_type": "image/png", "total_bytes": len(png)}


def get_id(resp):
    try:
        j = resp.json()
    except Exception:
        return None
    d = j.get("data") or j
    return d.get("id") or d.get("media_id_string") or d.get("media_id")


init = requests.post(BASE + "/initialize", auth=oauth, json=body, timeout=60)
print("INIT:", init.status_code, init.text[:250])
mid = get_id(init)
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
