import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:test")
os.environ.setdefault("X_API_KEY", "k")
os.environ.setdefault("X_API_SECRET", "s")
os.environ.setdefault("X_ACCESS_TOKEN", "t")
os.environ.setdefault("X_ACCESS_SECRET", "ts")

import main  # noqa: E402


def _response(status, payload):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


class FakeMediaApi:
    """Mimics the X v2 media upload API for a video that needs processing."""

    def __init__(self, states=("pending", "in_progress", "succeeded")):
        self.states = list(states)
        self.status_calls = []

    def post(self, url, **kwargs):
        if url.endswith("/initialize"):
            return _response(200, {"data": {"id": "9001"}})
        if url.endswith("/append"):
            return _response(204, {})
        if url.endswith("/finalize"):
            return _response(
                200,
                {"data": {"id": "9001", "processing_info": {"state": self.states.pop(0), "check_after_secs": 0}}},
            )
        return _response(404, {"error": "unknown endpoint"})

    def get(self, url, params=None, **kwargs):
        self.status_calls.append((url, params))
        # The real API only serves status at GET /2/media/upload?command=STATUS&media_id=...
        if url != main.MEDIA_URL or not params or params.get("command") != "STATUS":
            return _response(404, {"error": "not found"})
        state = self.states.pop(0)
        info = {"state": state}
        if state in ("pending", "in_progress"):
            info["check_after_secs"] = 0
        if state == "failed":
            info["error"] = {"message": "invalid codec"}
        return _response(200, {"data": {"id": "9001", "processing_info": info}})


class MediaUploadTests(unittest.TestCase):
    def _upload(self, api):
        with patch.object(main, "requests", api), patch.object(main.time, "sleep"):
            return main._upload("/dev/null", "video/mp4", "tweet_video")

    def test_video_waits_for_processing_via_status_endpoint(self):
        api = FakeMediaApi(states=("pending", "in_progress", "succeeded"))

        media_id = self._upload(api)

        self.assertEqual(media_id, "9001")
        self.assertEqual(len(api.status_calls), 2)
        for url, params in api.status_calls:
            self.assertEqual(url, main.MEDIA_URL)
            self.assertEqual(params, {"command": "STATUS", "media_id": "9001"})

    def test_video_processing_failure_raises(self):
        api = FakeMediaApi(states=("pending", "failed"))

        with self.assertRaises(RuntimeError) as ctx:
            self._upload(api)

        self.assertIn("invalid codec", str(ctx.exception))

    def test_status_endpoint_error_raises_instead_of_posting_unready_media(self):
        api = FakeMediaApi(states=("pending",))
        api.get = lambda url, params=None, **kwargs: _response(404, {"error": "not found"})

        with self.assertRaises(RuntimeError):
            self._upload(api)

    def test_image_without_processing_info_returns_immediately(self):
        api = FakeMediaApi(states=(None,))
        api.post = lambda url, **kwargs: _response(
            200, {"data": {"id": "9001"}} if not url.endswith("/append") else {}
        )

        media_id = self._upload(api)

        self.assertEqual(media_id, "9001")
        self.assertEqual(api.status_calls, [])


if __name__ == "__main__":
    unittest.main()
