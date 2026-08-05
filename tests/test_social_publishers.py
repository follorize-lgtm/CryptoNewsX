import json
import tempfile
import time
import unittest
from pathlib import Path

from social_publishers import (
    AccountStore,
    InstagramPublisher,
    TikTokPublisher,
    discover_facebook_instagram_accounts,
    tiktok_chunk_plan,
)


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self.payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = text or json.dumps(self.payload)

    def json(self):
        return self.payload


class DiscoverySession:
    @staticmethod
    def get(url, **kwargs):
        return FakeResponse(
            {
                "data": [
                    {
                        "name": "Page One",
                        "access_token": "page-token-1",
                        "instagram_business_account": {"id": "101", "username": "one"},
                    },
                    {
                        "name": "No Instagram Here",
                        "access_token": "page-token-2",
                    },
                    {
                        "name": "Page Two",
                        "access_token": "page-token-3",
                        "instagram_business_account": {"id": "202", "username": "two"},
                    },
                ]
            }
        )


class InstagramSession:
    def __init__(self):
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if url.endswith("/777/media"):
            return FakeResponse(
                {"id": "container-1", "uri": "https://upload.test/container-1"}
            )
        if url == "https://upload.test/container-1":
            return FakeResponse({"success": True})
        if url.endswith("/777/media_publish"):
            return FakeResponse({"id": "ig-post-1"})
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return FakeResponse({"status_code": "FINISHED"})


class TikTokSession:
    def __init__(self):
        self.posts = []
        self.puts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if url.endswith("/creator_info/query/"):
            return FakeResponse(
                {
                    "data": {"privacy_level_options": ["SELF_ONLY"]},
                    "error": {"code": "ok", "message": ""},
                }
            )
        if url.endswith("/video/init/"):
            return FakeResponse(
                {
                    "data": {
                        "publish_id": "tt-post-1",
                        "upload_url": "https://upload.tiktok.test/1",
                    },
                    "error": {"code": "ok", "message": ""},
                }
            )
        raise AssertionError(f"unexpected POST {url}")

    def put(self, url, **kwargs):
        self.puts.append((url, kwargs))
        return FakeResponse(status_code=201)


class SocialPublisherTests(unittest.TestCase):
    def test_account_store_upserts_without_losing_other_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountStore(Path(directory) / "accounts.json")
            store.upsert("instagram", {"name": "one", "user_id": "1", "enabled": True})
            store.upsert("instagram", {"name": "two", "user_id": "2", "enabled": True})
            store.upsert("instagram", {"name": "one", "access_token": "new-token"})

            data = store.load()
            self.assertEqual([row["name"] for row in data["instagram"]], ["one", "two"])
            self.assertEqual(data["instagram"][0]["user_id"], "1")
            self.assertEqual(data["instagram"][0]["access_token"], "new-token")

    def test_bulk_meta_discovery_finds_all_linked_instagram_accounts(self):
        rows = discover_facebook_instagram_accounts(
            "user-token", session=DiscoverySession()
        )

        self.assertEqual([row["name"] for row in rows], ["one", "two"])
        self.assertEqual(rows[0]["access_token"], "page-token-1")
        self.assertEqual(rows[1]["user_id"], "202")

    def test_tiktok_chunk_plan_keeps_final_chunk_within_api_limit(self):
        chunk_size, chunks = tiktok_chunk_plan(200 * 1024 * 1024)

        self.assertEqual(chunk_size, 64 * 1024 * 1024)
        self.assertEqual(
            [length // (1024 * 1024) for _offset, length in chunks], [64, 64, 72]
        )
        self.assertEqual(sum(length for _offset, length in chunks), 200 * 1024 * 1024)

    def test_instagram_resumable_upload_and_publish(self):
        session = InstagramSession()
        publisher = InstagramPublisher(
            session=session, poll_interval=0, poll_attempts=2
        )
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"video")
            result = publisher.publish_video(
                {
                    "name": "one",
                    "user_id": "777",
                    "access_token": "secret",
                    "api_base": "https://graph.instagram.com",
                    "api_version": "v24.0",
                },
                video,
                "caption",
            )

        self.assertEqual(result, "ig-post-1")
        self.assertEqual(session.posts[1][0], "https://upload.test/container-1")
        self.assertEqual(session.posts[1][1]["headers"]["file_size"], "5")
        self.assertTrue(session.posts[-1][0].endswith("/777/media_publish"))

    def test_tiktok_direct_upload_uses_creator_privacy_and_file_upload(self):
        session = TikTokSession()
        with tempfile.TemporaryDirectory() as directory:
            store = AccountStore(Path(directory) / "accounts.json")
            account = {
                "name": "ten",
                "access_token": "token",
                "refresh_token": "refresh",
                "expires_at": int(time.time()) + 3600,
                "mode": "direct",
                "privacy_level": "SELF_ONLY",
            }
            store.upsert("tiktok", account)
            publisher = TikTokPublisher(
                store, session=session, client_key="key", client_secret="secret"
            )
            video_path = Path(directory) / "video.mp4"
            video_path.write_bytes(b"video")

            result = publisher.publish_video(account, video_path, "caption")

        self.assertEqual(result, "tt-post-1")
        init_body = session.posts[1][1]["json"]
        self.assertEqual(init_body["post_info"]["privacy_level"], "SELF_ONLY")
        self.assertEqual(init_body["source_info"]["source"], "FILE_UPLOAD")
        self.assertEqual(session.puts[0][1]["headers"]["Content-Range"], "bytes 0-4/5")


if __name__ == "__main__":
    unittest.main()
