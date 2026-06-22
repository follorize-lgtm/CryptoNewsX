import asyncio
import logging
import os
import tempfile
import time

import tweepy
from dotenv import load_dotenv
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

_keys = dict(
    consumer_key=os.environ["X_API_KEY"],
    consumer_secret=os.environ["X_API_SECRET"],
    access_token=os.environ["X_ACCESS_TOKEN"],
    access_token_secret=os.environ["X_ACCESS_SECRET"],
)
twitter = tweepy.Client(**_keys)
twitter_v1 = tweepy.API(
    tweepy.OAuth1UserHandler(
        _keys["consumer_key"],
        _keys["consumer_secret"],
        _keys["access_token"],
        _keys["access_token_secret"],
    )
)

_last_post = 0.0
_groups = {}


def _allowed(chat):
    if not CHANNELS:
        return True
    return str(chat.id) in CHANNELS or (chat.username or "") in CHANNELS


def _upload(path, category, chunked):
    media = twitter_v1.media_upload(filename=path, chunked=chunked, media_category=category)
    return media.media_id_string


async def _download_and_upload(tg_file, suffix, category, chunked=False):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    try:
        await tg_file.download_to_drive(tmp.name)
        return await asyncio.to_thread(_upload, tmp.name, category, chunked)
    except Exception as exc:
        log.error("media upload failed: %s", exc)
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


async def collect_media(msgs):
    photos, videos, gifs = [], [], []
    for m in msgs:
        if m.photo:
            f = await m.photo[-1].get_file()
            mid = await _download_and_upload(f, ".jpg", "tweet_image")
            if mid:
                photos.append(mid)
        elif m.video:
            f = await m.video.get_file()
            mid = await _download_and_upload(f, ".mp4", "tweet_video", chunked=True)
            if mid:
                videos.append(mid)
        elif m.animation:
            f = await m.animation.get_file()
            mid = await _download_and_upload(f, ".mp4", "tweet_gif", chunked=True)
            if mid:
                gifs.append(mid)
    if photos:
        return photos[:4]
    if videos:
        return videos[:1]
    if gifs:
        return gifs[:1]
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
