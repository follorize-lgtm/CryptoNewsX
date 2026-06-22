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

print("uploading test image to /2/media/upload ...")
r = requests.post(
    "https://api.x.com/2/media/upload",
    auth=oauth,
    files={"media": ("blob", png, "image/png")},
    data={"media_category": "tweet_image"},
    timeout=60,
)
print("STATUS:", r.status_code)
print("BODY:", r.text)
