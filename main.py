import asyncio
import logging
import os
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

twitter = tweepy.Client(
    consumer_key=os.environ["X_API_KEY"],
    consumer_secret=os.environ["X_API_SECRET"],
    access_token=os.environ["X_ACCESS_TOKEN"],
    access_token_secret=os.environ["X_ACCESS_SECRET"],
)

_last_post = 0.0


def _allowed(chat):
    if not CHANNELS:
        return True
    return str(chat.id) in CHANNELS or (chat.username or "") in CHANNELS


def _send(text):
    twitter.create_tweet(text=text)


async def publish(text):
    global _last_post
    gap = MIN_INTERVAL - (time.time() - _last_post)
    if gap > 0:
        await asyncio.sleep(gap)
    for attempt in range(3):
        try:
            await asyncio.to_thread(_send, text)
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


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if msg is None or not _allowed(msg.chat):
        return
    text = process(msg.text or msg.caption or "", MAX_HASHTAGS)
    if not text:
        return
    if len(text) > 280:
        text = text[:277].rstrip() + "..."
    if await publish(text):
        log.info("posted: %s", text.replace("\n", " ")[:80])


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post))
    log.info("watching %s", ", ".join(CHANNELS) if CHANNELS else "all channels the bot is in")
    app.run_polling(allowed_updates=["channel_post"])


if __name__ == "__main__":
    main()
