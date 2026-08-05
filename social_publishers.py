import concurrent.futures
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

MIB = 1024 * 1024
DEFAULT_ACCOUNTS_FILE = "/opt/cryptonewsx/data/accounts.json"
TIKTOK_API = "https://open.tiktokapis.com"


class PublishError(RuntimeError):
    pass


def _empty_accounts():
    return {"instagram": [], "tiktok": []}


def _safe_json(response, action):
    try:
        payload = response.json()
    except ValueError as exc:
        raise PublishError(
            f"{action} returned HTTP {response.status_code} without JSON"
        ) from exc
    if response.status_code >= 400:
        detail = payload.get("error", payload) if isinstance(payload, dict) else payload
        raise PublishError(
            f"{action} failed (HTTP {response.status_code}): {str(detail)[:500]}"
        )
    return payload


def _tiktok_json(response, action):
    payload = _safe_json(response, action)
    error = payload.get("error") or {}
    if error.get("code") not in (None, "", "ok"):
        raise PublishError(
            f"{action} failed: {error.get('code')}: {error.get('message', '')}"
        )
    return payload


def _truncate_utf16(value, limit):
    raw = (value or "").encode("utf-16-le")
    if len(raw) <= limit * 2:
        return value or ""
    return raw[: limit * 2].decode("utf-16-le", errors="ignore")


def tiktok_chunk_plan(size):
    if size <= 0:
        raise ValueError("video file is empty")
    if size < 5 * MIB:
        return size, [(0, size)]
    if size <= 64 * MIB:
        return size, [(0, size)]

    chunk_size = 64 * MIB
    count = max(1, size // chunk_size)
    chunks = []
    offset = 0
    for index in range(count):
        remaining = size - offset
        length = remaining if index == count - 1 else chunk_size
        chunks.append((offset, length))
        offset += length
    return chunk_size, chunks


class AccountStore:
    def __init__(self, path=None):
        self.path = Path(
            path or os.getenv("SOCIAL_ACCOUNTS_FILE", DEFAULT_ACCOUNTS_FILE)
        )
        self._lock = threading.RLock()

    def load(self):
        with self._lock:
            if not self.path.exists():
                return _empty_accounts()
            data = json.loads(self.path.read_text(encoding="utf-8"))
            result = _empty_accounts()
            for platform in result:
                rows = data.get(platform, [])
                if not isinstance(rows, list):
                    raise PublishError(f"{self.path}: {platform} must be a list")
                result[platform] = rows
            return result

    def save(self, data):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix="accounts-", suffix=".json", dir=self.path.parent
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, indent=2, sort_keys=True)
                    handle.write("\n")
                os.chmod(tmp_name, 0o600)
                os.replace(tmp_name, self.path)
                os.chmod(self.path, 0o600)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)

    def upsert(self, platform, record):
        if platform not in ("instagram", "tiktok"):
            raise ValueError(f"unsupported platform: {platform}")
        name = record.get("name")
        if not name:
            raise ValueError("account name is required")
        with self._lock:
            data = self.load()
            rows = data[platform]
            for index, existing in enumerate(rows):
                if existing.get("name") == name:
                    rows[index] = {**existing, **record}
                    break
            else:
                rows.append(record)
            self.save(data)

    def update(self, platform, name, values):
        with self._lock:
            data = self.load()
            for row in data[platform]:
                if row.get("name") == name:
                    row.update(values)
                    self.save(data)
                    return
            raise PublishError(f"configured {platform} account not found: {name}")


def discover_facebook_instagram_accounts(
    user_token, api_version="v24.0", session=requests
):
    response = session.get(
        f"https://graph.facebook.com/{api_version}/me/accounts",
        params={
            "fields": "name,access_token,instagram_business_account{id,username}",
            "access_token": user_token,
            "limit": 100,
        },
        timeout=60,
    )
    payload = _safe_json(response, "Meta account discovery")
    found = []
    for page in payload.get("data", []):
        instagram = page.get("instagram_business_account") or {}
        if not instagram.get("id") or not page.get("access_token"):
            continue
        found.append(
            {
                "name": instagram.get("username")
                or page.get("name")
                or instagram["id"],
                "user_id": str(instagram["id"]),
                "access_token": page["access_token"],
                "api_base": "https://graph.facebook.com",
                "api_version": api_version,
                "enabled": True,
            }
        )
    return found


def discover_direct_instagram_account(
    access_token, api_version="v24.0", session=requests
):
    response = session.get(
        f"https://graph.instagram.com/{api_version}/me",
        params={"fields": "user_id,username", "access_token": access_token},
        timeout=60,
    )
    payload = _safe_json(response, "Instagram account discovery")
    user_id = payload.get("user_id") or payload.get("id")
    if not user_id:
        raise PublishError("Instagram account discovery did not return a user id")
    return {
        "name": payload.get("username") or str(user_id),
        "user_id": str(user_id),
        "access_token": access_token,
        "api_base": "https://graph.instagram.com",
        "api_version": api_version,
        "enabled": True,
    }


def tiktok_authorize_url(client_key, redirect_uri, state, mode="direct"):
    scope = (
        "user.info.basic,video.publish"
        if mode == "direct"
        else "user.info.basic,video.upload"
    )
    query = urlencode(
        {
            "client_key": client_key,
            "scope": scope,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"https://www.tiktok.com/v2/auth/authorize/?{query}"


def exchange_tiktok_code(
    code, client_key, client_secret, redirect_uri, session=requests
):
    response = session.post(
        f"{TIKTOK_API}/v2/oauth/token/",
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    payload = _safe_json(response, "TikTok token exchange")
    if payload.get("error"):
        raise PublishError(
            f"TikTok token exchange failed: {payload.get('error_description', payload['error'])}"
        )
    return payload


class InstagramPublisher:
    def __init__(self, session=requests, poll_interval=3, poll_attempts=200):
        self.session = session
        self.poll_interval = poll_interval
        self.poll_attempts = poll_attempts

    def publish_video(self, account, video_path, caption):
        token = account["access_token"]
        user_id = account["user_id"]
        version = account.get("api_version") or os.getenv(
            "INSTAGRAM_API_VERSION", "v24.0"
        )
        base = account.get("api_base", "https://graph.instagram.com").rstrip("/")
        graph = f"{base}/{version}"
        auth = {"Authorization": f"Bearer {token}"}

        response = self.session.post(
            f"{graph}/{user_id}/media",
            headers=auth,
            data={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": _truncate_utf16(caption, 2200),
                "share_to_feed": "true",
            },
            timeout=60,
        )
        container = _safe_json(
            response, f"Instagram container creation for {account['name']}"
        )
        container_id = str(container.get("id") or "")
        if not container_id:
            raise PublishError("Instagram container creation returned no id")
        upload_url = (
            container.get("uri")
            or f"https://rupload.facebook.com/ig-api-upload/{version}/{container_id}"
        )

        size = os.path.getsize(video_path)
        with open(video_path, "rb") as video:
            upload = self.session.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {token}",
                    "offset": "0",
                    "file_size": str(size),
                    "Content-Length": str(size),
                    "Content-Type": "video/mp4",
                },
                data=video,
                timeout=900,
            )
        _safe_json(upload, f"Instagram video upload for {account['name']}")

        for _ in range(self.poll_attempts):
            status_response = self.session.get(
                f"{graph}/{container_id}",
                headers=auth,
                params={"fields": "status_code,status"},
                timeout=60,
            )
            status = _safe_json(
                status_response, f"Instagram status check for {account['name']}"
            )
            code = status.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(
                    f"Instagram processing failed for {account['name']}: {status.get('status', code)}"
                )
            time.sleep(self.poll_interval)
        else:
            raise PublishError(f"Instagram processing timed out for {account['name']}")

        published = self.session.post(
            f"{graph}/{user_id}/media_publish",
            headers=auth,
            data={"creation_id": container_id},
            timeout=60,
        )
        payload = _safe_json(published, f"Instagram publish for {account['name']}")
        return str(payload.get("id") or container_id)


class TikTokPublisher:
    def __init__(self, store, session=requests, client_key=None, client_secret=None):
        self.store = store
        self.session = session
        self.client_key = client_key or os.getenv("TIKTOK_CLIENT_KEY", "")
        self.client_secret = client_secret or os.getenv("TIKTOK_CLIENT_SECRET", "")

    def _refresh(self, account):
        if account.get("expires_at", 0) > time.time() + 300:
            return account["access_token"]
        if (
            not self.client_key
            or not self.client_secret
            or not account.get("refresh_token")
        ):
            raise PublishError(
                f"TikTok token for {account['name']} expired and cannot be refreshed"
            )
        response = self.session.post(
            f"{TIKTOK_API}/v2/oauth/token/",
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": account["refresh_token"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=60,
        )
        payload = _safe_json(response, f"TikTok token refresh for {account['name']}")
        if payload.get("error"):
            raise PublishError(
                f"TikTok token refresh failed for {account['name']}: {payload.get('error_description', payload['error'])}"
            )
        values = {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token", account["refresh_token"]),
            "expires_at": int(time.time()) + int(payload.get("expires_in", 86400)),
            "refresh_expires_at": int(time.time())
            + int(payload.get("refresh_expires_in", 31536000)),
        }
        account.update(values)
        self.store.update("tiktok", account["name"], values)
        return values["access_token"]

    def _creator_info(self, token, account_name):
        response = self.session.post(
            f"{TIKTOK_API}/v2/post/publish/creator_info/query/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={},
            timeout=60,
        )
        return _tiktok_json(response, f"TikTok creator info for {account_name}").get(
            "data", {}
        )

    def publish_video(self, account, video_path, caption):
        token = self._refresh(account)
        size = os.path.getsize(video_path)
        chunk_size, chunks = tiktok_chunk_plan(size)
        mode = account.get("mode", "direct")
        source = {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": chunk_size,
            "total_chunk_count": len(chunks),
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        if mode == "direct":
            creator = self._creator_info(token, account["name"])
            allowed = creator.get("privacy_level_options") or []
            privacy = account.get("privacy_level", "SELF_ONLY")
            if privacy not in allowed:
                raise PublishError(
                    f"TikTok privacy {privacy} is not allowed for {account['name']}; allowed: {', '.join(allowed) or 'none'}"
                )
            payload = {
                "post_info": {
                    "title": _truncate_utf16(caption, 2200),
                    "privacy_level": privacy,
                    "disable_duet": bool(account.get("disable_duet", False)),
                    "disable_comment": bool(account.get("disable_comment", False)),
                    "disable_stitch": bool(account.get("disable_stitch", False)),
                    "is_aigc": bool(account.get("is_aigc", False)),
                },
                "source_info": source,
            }
            endpoint = f"{TIKTOK_API}/v2/post/publish/video/init/"
        elif mode == "inbox":
            payload = {"source_info": source}
            endpoint = f"{TIKTOK_API}/v2/post/publish/inbox/video/init/"
        else:
            raise PublishError(f"unknown TikTok mode for {account['name']}: {mode}")

        initialized = _tiktok_json(
            self.session.post(endpoint, headers=headers, json=payload, timeout=60),
            f"TikTok upload initialization for {account['name']}",
        )
        data = initialized.get("data") or {}
        upload_url = data.get("upload_url")
        publish_id = data.get("publish_id")
        if not upload_url or not publish_id:
            raise PublishError(
                f"TikTok upload initialization returned incomplete data for {account['name']}"
            )

        with open(video_path, "rb") as video:
            for index, (offset, length) in enumerate(chunks):
                video.seek(offset)
                content = video.read(length)
                response = self.session.put(
                    upload_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(content)),
                        "Content-Range": f"bytes {offset}-{offset + len(content) - 1}/{size}",
                    },
                    data=content,
                    timeout=900,
                )
                expected = (200, 201, 206)
                if response.status_code not in expected:
                    raise PublishError(
                        f"TikTok video chunk {index + 1}/{len(chunks)} failed for {account['name']} "
                        f"(HTTP {response.status_code}): {response.text[:300]}"
                    )
        return str(publish_id)


class SocialPublisher:
    def __init__(self, store=None, session=requests, max_workers=None):
        self.store = store or AccountStore()
        self.instagram = InstagramPublisher(session=session)
        self.tiktok = TikTokPublisher(self.store, session=session)
        self.max_workers = max_workers or int(os.getenv("SOCIAL_MAX_WORKERS", "4"))

    def configured_count(self):
        data = self.store.load()
        return sum(
            1
            for platform in data.values()
            for account in platform
            if account.get("enabled", True)
        )

    def publish_video(self, video_path, caption):
        data = self.store.load()
        jobs = []
        for account in data["instagram"]:
            if account.get("enabled", True):
                jobs.append(("instagram", account, self.instagram.publish_video))
        for account in data["tiktok"]:
            if account.get("enabled", True):
                jobs.append(("tiktok", account, self.tiktok.publish_video))
        if not jobs:
            return []

        results = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        ) as pool:
            futures = {
                pool.submit(worker, account.copy(), video_path, caption): (
                    platform,
                    account["name"],
                )
                for platform, account, worker in jobs
            }
            for future in concurrent.futures.as_completed(futures):
                platform, name = futures[future]
                try:
                    post_id = future.result()
                    results.append(
                        {
                            "platform": platform,
                            "name": name,
                            "ok": True,
                            "post_id": post_id,
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "platform": platform,
                            "name": name,
                            "ok": False,
                            "error": str(exc),
                        }
                    )
        return results
