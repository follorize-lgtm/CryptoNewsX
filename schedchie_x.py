"""Cross-post Telegram channel messages to the connected Schedchie X account."""
import asyncio
import hashlib
import logging
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from schedchie_client import Client, SchedchieError, tool_json
from telegram_media import check_downloadable, media_source


log = logging.getLogger('crosspost.schedchie')
MEDIA_ATTEMPTS = 3


def configured():
    return all(os.getenv(name, '').strip() for name in (
        'SCHEDCHIE_CREDENTIALS_FILE', 'SCHEDCHIE_X_ACCOUNT_ID',
        'SCHEDCHIE_MEDIA_BASE_URL', 'SCHEDCHIE_MEDIA_UPLOAD_URL',
        'SCHEDCHIE_MEDIA_UPLOAD_TOKEN'))


@contextmanager
def _database():
    path = Path(os.getenv('SCHEDCHIE_X_STATE_DB', 'schedchie-x-state.sqlite3'))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS submissions (
        source TEXT PRIMARY KEY, state TEXT NOT NULL, post_id TEXT DEFAULT '',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, error TEXT DEFAULT '')''')
    try:
        with db:
            yield db
    finally:
        db.close()


def _client():
    return Client(Path(os.environ['SCHEDCHIE_CREDENTIALS_FILE']))


def _post_id(reply):
    if not isinstance(reply, dict):
        return ''
    for value in (reply, reply.get('post'), reply.get('data'), reply.get('result')):
        if isinstance(value, dict):
            for key in ('postId', 'id', '_id'):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate:
                    return candidate
    return ''


def _stage(path, extension, mime):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    key = f'wabble/x/tx_01/{digest}.{extension}'
    upload = os.environ['SCHEDCHIE_MEDIA_UPLOAD_URL'].rstrip('/') + '/media/' + key
    public = os.environ['SCHEDCHIE_MEDIA_BASE_URL'].rstrip('/') + '/media/' + key
    # Keys are content hashes, so a file that is already public is reused.
    # A relay that refuses overwrites must not block a repeated picture.
    try:
        if requests.head(public, timeout=30, allow_redirects=True).status_code == 200:
            return public
    except requests.RequestException:
        pass
    with Path(path).open('rb') as file:
        response = requests.put(upload, data=file, headers={
            'Authorization': 'Bearer ' + os.environ['SCHEDCHIE_MEDIA_UPLOAD_TOKEN'],
            'Content-Type': mime, 'Content-Length': str(Path(path).stat().st_size),
        }, timeout=120)
    if response.status_code in (409, 412):
        return public
    if response.status_code >= 400:
        raise SchedchieError(f'media relay upload HTTP {response.status_code}: {response.text[:200]}')
    return public


def _confirmed_media(reply):
    for value in (reply, reply.get('media'), reply.get('data'), reply.get('result'),
                  reply.get('mediaItem')):
        if isinstance(value, dict) and value.get('gcsObjectName'):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict) and value[0].get('gcsObjectName'):
            return value[0]
    return None


async def _media_descriptor(message, client):
    found = media_source(message)
    if not found:
        return None
    source, extension, mime, _kind = found
    check_downloadable(source)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / ('telegram.' + extension)
        await (await source.get_file()).download_to_drive(str(path))
        if path.stat().st_size > 200 * 1024 * 1024:
            raise ValueError('Telegram media exceeds the 200 MB relay limit')
        url = await asyncio.to_thread(_stage, path, extension, mime)
    result = await asyncio.to_thread(client.call, 'upload_media', {'url': url, 'mimeType': mime})
    media = _confirmed_media(tool_json(result))
    if not media:
        raise SchedchieError('Schedchie did not confirm the media upload')
    return media


async def _media_with_retry(message, client):
    """Staging and upload_media publish nothing, so they are safe to retry."""
    for attempt in range(1, MEDIA_ATTEMPTS + 1):
        try:
            return await _media_descriptor(message, client)
        except ValueError as exc:
            log.error('Schedchie X media skipped for message %s: %s', message.message_id, exc)
            return None
        except Exception as exc:
            if attempt == MEDIA_ATTEMPTS:
                log.error('Schedchie X media failed for message %s after %d attempts: %s',
                          message.message_id, attempt, exc)
                return None
            log.warning('Schedchie X media attempt %d failed for message %s: %s',
                        attempt, message.message_id, exc)
            await asyncio.sleep(2 * attempt)


def _source(messages):
    return f'{messages[0].chat.id}:{min(message.message_id for message in messages)}'


def _caption(text):
    text = text.strip()
    return text if len(text) <= 280 else text[:277].rstrip() + '...'


async def publish(messages, text):
    """Schedule at most once per Telegram source; uncertain outcomes need review."""
    source = _source(messages)
    with _database() as db:
        existing = db.execute('SELECT state,post_id FROM submissions WHERE source=?', (source,)).fetchone()
        if existing:
            return existing['post_id'] or 'already-' + existing['state']
    wanted = []
    for message in messages:
        found = media_source(message)
        if not found:
            continue
        kind = found[3]
        if kind == 'video':
            # X takes one video per post and cannot mix it with pictures.
            if not wanted:
                wanted.append(message)
            break
        if len(wanted) < 4:
            wanted.append(message)
    if not text and not wanted:
        return None
    client = await asyncio.to_thread(_client)
    media = []
    for message in wanted:
        descriptor = await _media_with_retry(message, client)
        if descriptor:
            media.append(descriptor)
    if len(media) < len(wanted):
        log.error('Schedchie X post for %s has %d of %d media; posting what uploaded',
                  source, len(media), len(wanted))
    if not text and not media:
        return None
    account_id = os.environ['SCHEDCHIE_X_ACCOUNT_ID']
    now = int(time.time())
    with _database() as db:
        db.execute('INSERT OR IGNORE INTO submissions(source,state,created_at,updated_at) VALUES(?,?,?,?)',
                   (source, 'submitting', now, now))
        if db.total_changes == 0:
            return 'already-submitting'
    body = {
        'text': _caption(text) or 'News update',
        'accountIds': [account_id],
        'scheduledTime': (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'platformSpecificContent': {account_id: {'x_text': _caption(text) or 'News update'}},
    }
    if media:
        body['media'] = media
    result = await asyncio.to_thread(client.call, 'schedule_post', body)
    post_id = _post_id(tool_json(result))
    if not post_id:
        raise SchedchieError('Schedchie did not return a post ID; review before retrying')
    with _database() as db:
        db.execute("UPDATE submissions SET state='scheduled',post_id=?,updated_at=? WHERE source=?",
                   (post_id, int(time.time()), source))
    return post_id


def _failure_reason(post):
    reasons = []
    for item in post.get('publishResults') or []:
        if isinstance(item, dict) and item.get('success') is not True:
            reasons.append(str(item.get('error') or item.get('message') or item.get('errorMessage') or item))
    reasons.append(str(post.get('error') or post.get('errorMessage') or ''))
    return '; '.join(r for r in reasons if r)[:500] or 'no reason given'


def reconcile(limit=25):
    """Check remote delivery; a failed post is recorded, never blindly resubmitted."""
    with _database() as db:
        rows = db.execute("SELECT source,post_id FROM submissions WHERE state='scheduled' AND post_id<>'' ORDER BY updated_at LIMIT ?", (limit,)).fetchall()
    if not rows:
        return 0
    client = _client()
    changed = 0
    for row in rows:
        try:
            reply = tool_json(client.call('get_post', {'postId': row['post_id']}))
            post = reply.get('post') if isinstance(reply.get('post'), dict) else reply
            status = str(post.get('status') or post.get('processingStatus') or '').upper()
            results = post.get('publishResults')
            if status in {'PUBLISHED', 'PUBLISH_COMPLETE', 'LIVE'} or (
                    status == 'COMPLETED' and post.get('isPublished') is True and
                    isinstance(results, list) and results and
                    all(isinstance(item, dict) and item.get('success') is True for item in results)):
                state = 'published'
            elif status in {'FAILED', 'PARTIALLY_FAILED'} or (
                    status == 'COMPLETED' and isinstance(results, list) and results and
                    all(isinstance(item, dict) and item.get('success') is False for item in results)):
                state = 'failed'
                log.error('Schedchie X post %s failed on X: %s', row['post_id'], _failure_reason(post))
            else:
                continue
            with _database() as db:
                db.execute('UPDATE submissions SET state=?,updated_at=? WHERE source=?',
                           (state, int(time.time()), row['source']))
            changed += 1
            log.info('Schedchie X delivery %s source=%s post=%s', state, row['source'], row['post_id'])
        except (SchedchieError, ValueError, TypeError):
            log.exception('Schedchie X status check failed for source=%s', row['source'])
    return changed
