# CryptoNewsX

A Telegram bot that watches a channel and reposts new messages to an X account.
Before posting it strips `@` mentions, removes emojis, drops promo/`t.me` lines,
and turns up to two relevant keywords into inline hashtags.

## How it works

The bot must be an **admin** of the source channel. Once it is, Telegram delivers
every new channel post to the bot, which cleans the text and posts it to X.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.

2. Add the bot to your channel as an **administrator** (it does not need any
   permissions beyond being a member admin to receive posts). In BotFather also
   run `/setprivacy` is not required for channels, but make sure the bot is admin.

3. Install dependencies:

       python3 -m venv venv
       source venv/bin/activate
       pip install -r requirements.txt

4. Copy `.env.example` to `.env` and fill in the values (see below).

5. Run it:

       python main.py

## Configuration

| Variable | What it is |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `OWNER_TELEGRAM_IDS` | Comma-separated numeric Telegram user IDs allowed to use `/setup` in private chat |
| `OWNER_TELEGRAM_ID` | Backward-compatible single-owner variable; can also be used together with `OWNER_TELEGRAM_IDS` |
| `SOURCE_CHANNELS` | Comma-separated channel ids or usernames to allow (leave empty to accept every channel the bot is in) |
| `X_API_KEY` / `X_API_SECRET` | X app consumer keys |
| `X_ACCESS_TOKEN` / `X_ACCESS_SECRET` | X access token for the posting account (needs Read and Write) |
| `MAX_HASHTAGS` | Max inline hashtags per post (default 2) |
| `MIN_INTERVAL_SECONDS` | Minimum gap between posts (default 45) |

Example with multiple `/setup` owners:

    OWNER_TELEGRAM_IDS=123456789,987654321,555555555

Every ID in that list gets the same owner-only `/setup` access, and `/setup` still only works in a private chat with the bot.

To find a channel id, forward a channel post to [@userinfobot](https://t.me/userinfobot)
or read it from the bot logs. Channel ids look like `-1001234567890`.

The hashtag keyword list lives in `processor.py` (`TERMS`). Add or remove terms
there to control what gets tagged.

## Running on a VPS

    sudo useradd -r -m -d /opt/cryptonewsx cnx
    sudo -u cnx git clone <repo-url> /opt/cryptonewsx
    cd /opt/cryptonewsx
    sudo -u cnx python3 -m venv venv
    sudo -u cnx venv/bin/pip install -r requirements.txt
    sudo -u cnx cp .env.example .env   # then edit .env
    sudo cp cryptonewsx.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now cryptonewsx
    sudo systemctl status cryptonewsx
    journalctl -u cryptonewsx -f
