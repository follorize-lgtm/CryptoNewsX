import os
import unittest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:test")
os.environ.setdefault("X_API_KEY", "k")
os.environ.setdefault("X_API_SECRET", "s")
os.environ.setdefault("X_ACCESS_TOKEN", "t")
os.environ.setdefault("X_ACCESS_SECRET", "ts")

import main  # noqa: E402


class BatchTests(unittest.TestCase):
    def test_single_photo_one_post(self):
        self.assertEqual(main._batch([("1", "photo")]), [["1"]])

    def test_all_photos_in_one_post(self):
        items = [(str(i), "photo") for i in range(4)]
        self.assertEqual(main._batch(items), [["0", "1", "2", "3"]])

    def test_more_than_four_photos_capped_to_single_post(self):
        items = [(str(i), "photo") for i in range(10)]
        groups = main._batch(items)
        self.assertEqual(groups, [["0", "1", "2", "3"]])

    def test_video_gets_own_post(self):
        self.assertEqual(main._batch([("v", "video")]), [["v"]])

    def test_photos_and_video_photos_first_in_one_post(self):
        items = [("1", "photo"), ("v", "video"), ("2", "photo"), ("3", "photo")]
        groups = main._batch(items)
        self.assertEqual(groups, [["1", "2", "3"], ["v"]])

    def test_empty(self):
        self.assertEqual(main._batch([]), [])


if __name__ == "__main__":
    unittest.main()
