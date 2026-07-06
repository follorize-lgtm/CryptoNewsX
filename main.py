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

from poster import is_too_long_error, split_thread
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
THREAD_INTERVAL = float(os.getenv("THREAD_INTERVAL_SECONDS", "2"))

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


def _processing(payload):
    return (payload.get("data") or payload).get("processing_info")


def _init_media(size, media_type, category):
    body = {"media_category": category, "media_type": media_type, "total_bytes": size}
    r = requests.post(MEDIA_URL + "/initialize", auth=_oauth, json=body, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError("init %s %s" % (r.status_code, r.text[:200]))
    return _media_id(r.json())


def _upload(path, media_type, category):
    size = os.path.getsize(path)
    media_id = _init_media(size, media_type, category)
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
    info = _processing(fin.json())
    while info and info.get("state") in ("pending", "in_progress"):
        time.sleep(info.get("check_after_secs", 3))
        st = requests.get("%s/%s" % (MEDIA_URL, media_id), auth=_oauth, timeout=60)
        if st.status_code >= 400:
            break
        info = _processing(st.json())
    return media_id


async def _grab(tg_file, suffix, media_type, category):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    try:
        await tg_file.download_to_drive(tmp.name)
        return await asyncio.to_thread(_upload, tmp.name, media_type, category)
    except Exception as exc:
        log.error("media failed: %s", exc)
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


async def collect_media(msgs):
    items = []
    for m in msgs:
        if m.photo:
            mid = await _grab(await m.photo[-1].get_file(), ".jpg", "image/jpeg", "tweet_image")
            if mid:
                items.append((mid, "photo"))
        elif m.video or m.animation:
            src = m.video or m.animation
            mid = await _grab(await src.get_file(), ".mp4", "video/mp4", "tweet_video")
            if mid:
                items.append((mid, "video"))
    return items


def _batch(items):
    groups = []
    photos = []
    for mid, kind in items:
        if kind == "photo":
            photos.append(mid)
            if len(photos) == 4:
                groups.append(photos)
                photos = []
        else:
            if photos:
                groups.append(photos)
                photos = []
            groups.append([mid])
    if photos:
        groups.append(photos)
    return groups


def _send(text, media_ids, reply_to):
    kwargs = {}
    if text:
        kwargs["text"] = text
    if media_ids:
        kwargs["media_ids"] = media_ids
    if reply_to:
        kwargs["in_reply_to_tweet_id"] = reply_to
    resp = twitter.create_tweet(**kwargs)
    return resp.data["id"]


async def _post(text, media_ids, reply_to, raise_too_long=False):
    for attempt in range(3):
        try:
            return await asyncio.to_thread(_send, text, media_ids, reply_to)
        except tweepy.TooManyRequests:
            wait = 60 * (attempt + 1)
            log.warning("rate limited, backing off %ds", wait)
            await asyncio.sleep(wait)
        except Exception as exc:
            if raise_too_long and is_too_long_error(exc):
                raise
            log.error("post failed: %s", exc)
            await asyncio.sleep(5 * (attempt + 1))
    return None


async def _post_replies(posts, reply_to=None):
    posted = 0
    current_reply_to = reply_to
    post_list = list(posts)
    for index, (body, media_ids) in enumerate(post_list):
        tweet_id = await _post(body, media_ids, current_reply_to)
        if not tweet_id:
            break
        current_reply_to = tweet_id
        posted += 1
        if index < len(post_list) - 1 and THREAD_INTERVAL > 0:
            await asyncio.sleep(THREAD_INTERVAL)
    return posted


async def publish(text, groups):
    global _last_post
    gap = MIN_INTERVAL - (time.time() - _last_post)
    if gap > 0:
        await asyncio.sleep(gap)

    media_groups = groups or [[]]
    if not text:
        posted = await _post_replies((None, media_ids) for media_ids in media_groups)
        if posted:
            _last_post = time.time()
        return posted

    try:
        first_id = await _post(text, media_groups[0], None, raise_too_long=True)
    except Exception as exc:
        if not is_too_long_error(exc):
            log.error("post failed: %s", exc)
            return 0
        parts = split_thread(text)
        if len(parts) <= 1:
            log.error("post failed: %s", exc)
            return 0
        posts = []
        for index, part in enumerate(parts):
            media_ids = media_groups[index] if index < len(media_groups) else []
            posts.append((part, media_ids))
        for media_ids in media_groups[len(parts):]:
            posts.append((None, media_ids))
        posted = await _post_replies(posts)
    else:
        if not first_id:
            return 0
        posted = 1
        reply_to = first_id
        for media_ids in media_groups[1:]:
            tweet_id = await _post(None, media_ids, reply_to)
            if not tweet_id:
                break
            reply_to = tweet_id
            posted += 1

    if posted:
        _last_post = time.time()
    return posted


async def handle(msgs):
    raw = ""
    for m in msgs:
        raw = m.text or m.caption or ""
        if raw:
            break
    text = process(raw, MAX_HASHTAGS)
    items = await collect_media(msgs)
    groups = _batch(items)
    if not text and not groups:
        return
    posted = await publish(text, groups)
    if posted:
        log.info("posted: %s [%d media / %d tweet(s)]", text.replace("\n", " ")[:80], len(items), posted)


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
