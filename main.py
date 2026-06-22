import asyncio
import logging
import os
import tempfile
import time

import requests
import tweepy
from dotenv import load_dotenv
from requests_oauthlib import OAuth1
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from processor import process

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("crosspost")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNELS = {c.strip().lstrip("@") for c in os.environ.get("SOURCE_CHANNELS", "").split(",") if c.strip()}
MAX_HASHTAGS = int(os.getenv("MAX_HASHTAGS", "2"))
MIN_INTERVAL = int(os.getenv("MIN_INTERVAL_SECONDS", "45"))
GROUP_WAIT = float(os.getenv("ALBUM_WAIT_SECONDS", "2"))

twitter = tweepy.Client(
    consumer_key=os.environ["X_API_KEY"],
    consumer_secret=os.environ["X_API_SECRET"],
    access_token=os.environ["X_ACCESS_TOKEN"],
    access_token_secret=os.environ["X_ACCESS_SECRET"],
)
_oauth = OAuth1(
    os.environ["X_API_KEY"],
    os.environ["X_API_SECRET"],
    os.environ["X_ACCESS_TOKEN"],
    os.environ["X_ACCESS_SECRET"],
)
MEDIA_URL = "https://api.x.com/2/media/upload"

_last_post = 0.0
_groups = {}


def _allowed(chat):
    if not CHANNELS:
        return True
    return str(chat.id) in CHANNELS or (chat.username or "") in CHANNELS


def _media_id(payload):
    data = payload.get("data", payload)
    return str(data.get("id") or data.get("media_id_string") or data.get("media_id"))


def _upload_image(path):
    with open(path, "rb") as fh:
        r = requests.post(
            MEDIA_URL,
            auth=_oauth,
            files={"media": ("blob", fh, "image/jpeg")},
            data={"media_category": "tweet_image"},
            timeout=120,
        )
    if r.status_code >= 400:
        raise RuntimeError("%s %s" % (r.status_code, r.text[:200]))
    return _media_id(r.json())


def _upload_video(path):
    size = os.path.getsize(path)
    init = requests.post(
        MEDIA_URL + "/initialize",
        auth=_oauth,
        json={"media_category": "tweet_video", "media_type": "video/mp4", "total_bytes": size},
        timeout=60,
    )
    if init.status_code >= 400:
        raise RuntimeError("init %s %s" % (init.status_code, init.text[:200]))
    media_id = _media_id(init.json())
    with open(path, "rb") as fh:
        idx = 0
        while True:
            chunk = fh.read(4 * 1024 * 1024)
            if not chunk:
                break
            ap = requests.post(
                "%s/%s/append" % (MEDIA_URL, media_id),
                auth=_oauth,
                data={"segment_index": idx},
                files={"media": ("blob", chunk, "application/octet-stream")},
                timeout=180,
            )
            if ap.status_code >= 400:
                raise RuntimeError("append %s %s" % (ap.status_code, ap.text[:200]))
            idx += 1
    fin = requests.post("%s/%s/finalize" % (MEDIA_URL, media_id), auth=_oauth, timeout=60)
    if fin.status_code >= 400:
        raise RuntimeError("finalize %s %s" % (fin.status_code, fin.text[:200]))
    info = (fin.json().get("data") or {}).get("processing_info")
    while info and info.get("state") in ("pending", "in_progress"):
        time.sleep(info.get("check_after_secs", 3))
        st = requests.get("%s/%s" % (MEDIA_URL, media_id), auth=_oauth, timeout=60)
        if st.status_code >= 400:
            break
        info = (st.json().get("data") or {}).get("processing_info")
    return media_id


async def _grab(tg_file, suffix, uploader):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    try:
        await tg_file.download_to_drive(tmp.name)
        return await asyncio.to_thread(uploader, tmp.name)
    except Exception as exc:
        log.error("media failed: %s", exc)
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


async def collect_media(msgs):
    photos, videos = [], []
    for m in msgs:
        if m.photo:
            mid = await _grab(await m.photo[-1].get_file(), ".jpg", _upload_image)
            if mid:
                photos.append(mid)
        elif m.video or m.animation:
            src = m.video or m.animation
            mid = await _grab(await src.get_file(), ".mp4", _upload_video)
            if mid:
                videos.append(mid)
    if photos:
        return photos[:4]
    if videos:
        return videos[:1]
    return []


def _send(text, media_ids):
    kwargs = {}
    if text:
        kwargs["text"] = text
    if media_ids:
        kwargs["media_ids"] = media_ids
    twitter.create_tweet(**kwargs)


async def publish(text, media_ids):
    global _last_post
    gap = MIN_INTERVAL - (time.time() - _last_post)
    if gap > 0:
        await asyncio.sleep(gap)
    for attempt in range(3):
        try:
            await asyncio.to_thread(_send, text, media_ids)
            _last_post = time.time()
            return True
        except tweepy.TooManyRequests:
            wait = 60 * (attempt + 1)
            log.warning("rate limited, backing off %ds", wait)
            await asyncio.sleep(wait)
        except Exception as exc:
            log.error("post failed: %s", exc)
            await asyncio.sleep(5 * (attempt + 1))
    return False


async def handle(msgs):
    raw = ""
    for m in msgs:
        raw = m.text or m.caption or ""
        if raw:
            break
    text = process(raw, MAX_HASHTAGS)
    media_ids = await collect_media(msgs)
    if not text and not media_ids:
        return
    if len(text) > 280:
        text = text[:277].rstrip() + "..."
    if await publish(text, media_ids):
        log.info("posted: %s [%d media]", text.replace("\n", " ")[:80], len(media_ids))


async def flush_group(gid):
    try:
        await asyncio.sleep(GROUP_WAIT)
    except asyncio.CancelledError:
        return
    group = _groups.pop(gid, None)
    if not group:
        return
    await handle(sorted(group["msgs"], key=lambda m: m.message_id))


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if msg is None or not _allowed(msg.chat):
        return
    if msg.media_group_id:
        group = _groups.setdefault(msg.media_group_id, {"msgs": [], "task": None})
        group["msgs"].append(msg)
        if group["task"]:
            group["task"].cancel()
        group["task"] = asyncio.create_task(flush_group(msg.media_group_id))
        return
    await handle([msg])


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post))
    log.info("watching %s", ", ".join(CHANNELS) if CHANNELS else "all channels the bot is in")
    app.run_polling(allowed_updates=["channel_post"])


if __name__ == "__main__":
    main()
