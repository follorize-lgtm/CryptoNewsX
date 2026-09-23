"""Optional Publer delivery for the existing Telegram-to-X cross-poster."""
import os
import sqlite3
import tempfile
from pathlib import Path

import requests


API = "https://app.publer.com/api/v1"


def configured():
    return all(os.getenv(name, "").strip() for name in (
        "PUBLER_API_KEY", "PUBLER_WORKSPACE_ID", "PUBLER_X_ACCOUNT_ID"))


def request(method, path, **kwargs):
    response = requests.request(method, API + path, timeout=60, headers={
        "Authorization": "Bearer-API " + os.environ["PUBLER_API_KEY"],
        "Publer-Workspace-Id": os.environ["PUBLER_WORKSPACE_ID"],
    }, **kwargs)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and (data.get("errors") or data.get("success") is False):
        raise RuntimeError("Publer rejected the request")
    return data


def _database():
    path = Path(os.getenv("PUBLER_X_STATE_DB", "publer-x-state.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS submissions (source TEXT PRIMARY KEY, state TEXT NOT NULL, job_id TEXT)")
    return db


async def _upload(message):
    source = message.photo[-1] if message.photo else message.video or message.animation
    if not source:
        return None
    suffix = ".jpg" if message.photo else ".mp4"
    kind = "image" if message.photo else "video"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / ("telegram" + suffix)
        await (await source.get_file()).download_to_drive(str(path))
        if path.stat().st_size > 200 * 1024 * 1024:
            raise ValueError("Publer direct upload limit is 200 MB")
        with path.open("rb") as file:
            media = request("POST", "/media", files={"file": (path.name, file)},
                            data={"direct_upload": "true", "in_library": "false"})
    if not isinstance(media, dict) or not media.get("id"):
        raise RuntimeError("Publer media upload returned no ID")
    return {"id": media["id"], "type": kind}


async def publish(messages, text):
    """Submit once per Telegram source; uncertain submissions require manual review."""
    source = str(messages[0].chat.id) + ":" + str(messages[0].message_id)
    with _database() as db:
        if db.execute("SELECT 1 FROM submissions WHERE source=?", (source,)).fetchone():
            return "already-submitted"
    media = []
    for message in messages:
        if message.photo and len(media) >= 4:
            continue
        item = await _upload(message)
        if item:
            media.append(item)
    if not text and not media:
        return None
    long_post = os.getenv("PUBLER_X_LONG_POST", "false").lower() == "true"
    if len(text) > 280 and not long_post:
        raise ValueError("X text exceeds 280 characters; enable long posts only for X Premium")
    network = {"type": "video" if media and media[0]["type"] == "video" else "photo" if media else "status",
               "text": text}
    if media:
        network["media"] = media
    if long_post and len(text) > 280:
        network["details"] = {"type": "long_post"}
    body = {"bulk": {"state": "scheduled", "posts": [{"networks": {"twitter": network},
            "accounts": [{"id": os.environ["PUBLER_X_ACCOUNT_ID"]}]}]}}
    with _database() as db:
        db.execute("INSERT OR IGNORE INTO submissions(source,state) VALUES(?,'submitting')", (source,))
    # Never retry a request with an uncertain outcome: it could duplicate an X post.
    result = request("POST", "/posts/schedule/publish", json=body)
    job = result.get("job_id") or (result.get("data") or {}).get("job_id")
    if not job:
        raise RuntimeError("Publer submission returned no job ID; review dashboard")
    with _database() as db:
        db.execute("UPDATE submissions SET state='working',job_id=? WHERE source=?", (job, source))
    return job
