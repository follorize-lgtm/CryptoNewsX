# CryptoNewsX

Watches one or more Telegram channels and reposts new messages to an X account.
Before posting it strips `@` mentions, removes emojis, drops promo/`t.me` lines,
and turns up to two relevant keywords into inline hashtags.

## Setup

1. Install dependencies:

       python3 -m venv venv
       source venv/bin/activate
       pip install -r requirements.txt

2. Copy `.env.example` to `.env` and fill in the values (see below).

3. Generate a Telegram session string once (run on your own machine, it asks
   for your phone number and the login code):

       python session_setup.py

   Paste the printed `TELEGRAM_SESSION=...` line into `.env`.

4. Run it:

       python main.py

## Configuration

| Variable | What it is |
| --- | --- |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | Telegram app credentials |
| `TELEGRAM_SESSION` | Session string from `session_setup.py` |
| `SOURCE_CHANNELS` | Comma-separated channel usernames or numeric ids |
| `X_API_KEY` / `X_API_SECRET` | X app consumer keys |
| `X_ACCESS_TOKEN` / `X_ACCESS_SECRET` | X access token for the posting account |
| `MAX_HASHTAGS` | Max inline hashtags per post (default 2) |
| `MIN_INTERVAL_SECONDS` | Minimum gap between posts (default 45) |

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
