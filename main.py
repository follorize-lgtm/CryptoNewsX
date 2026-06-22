import asyncio
import logging
import os
import time

import tweepy
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from processor import process

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("crosspost")

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION = os.environ["TELEGRAM_SESSION"]
CHANNELS = [c.strip() for c in os.environ["SOURCE_CHANNELS"].split(",") if c.strip()]
MAX_HASHTAGS = int(os.getenv("MAX_HASHTAGS", "2"))
MIN_INTERVAL = int(os.getenv("MIN_INTERVAL_SECONDS", "45"))

twitter = tweepy.Client(
    consumer_key=os.environ["X_API_KEY"],
    consumer_secret=os.environ["X_API_SECRET"],
    access_token=os.environ["X_ACCESS_TOKEN"],
    access_token_secret=os.environ["X_ACCESS_SECRET"],
)

_last_post = 0.0


def _channel(value):
    return int(value) if value.lstrip("-").isdigit() else value


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


client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)


@client.on(events.NewMessage(chats=[_channel(c) for c in CHANNELS]))
async def on_message(event):
    text = process(event.message.message or "", MAX_HASHTAGS)
    if not text:
        return
    if len(text) > 280:
        text = text[:277].rstrip() + "..."
    if await publish(text):
        log.info("posted: %s", text.replace("\n", " ")[:80])


def main():
    client.start()
    log.info("watching %d channel(s)", len(CHANNELS))
    client.run_until_disconnected()


if __name__ == "__main__":
    main()
