import unittest

from poster import send_text, split_thread


class TooLongError(Exception):
    pass


class FakeClient:
    def __init__(self, reject_long=False, limit=280):
        self.reject_long = reject_long
        self.limit = limit
        self.calls = []

    def create_tweet(self, **kwargs):
        text = kwargs["text"]
        if self.reject_long and len(text) > self.limit:
            raise TooLongError("Post needs to be a bit shorter: over 280 character limit")
        tweet_id = str(len(self.calls) + 1)
        self.calls.append({**kwargs, "id": tweet_id})
        return {"data": {"id": tweet_id}}


class PosterTests(unittest.TestCase):
    def test_send_text_posts_longform_once_when_api_accepts_it(self):
        client = FakeClient(reject_long=False)
        text = "Alpha " * 80

        posted_count = send_text(client, text, thread_delay=0)

        self.assertEqual(posted_count, 1)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["text"], text.strip())

    def test_send_text_threads_when_api_rejects_long_text(self):
        client = FakeClient(reject_long=True)
        text = "Alpha " * 90

        posted_count = send_text(client, text, thread_delay=0)

        self.assertGreater(posted_count, 1)
        self.assertTrue(all(len(call["text"]) <= 280 for call in client.calls))
        self.assertEqual(sum(call["text"].count("Alpha") for call in client.calls), 90)
        self.assertEqual(client.calls[1]["in_reply_to_tweet_id"], "1")

    def test_split_thread_keeps_short_text_as_one_post(self):
        self.assertEqual(split_thread("Short post", 280), ["Short post"])


if __name__ == "__main__":
    unittest.main()
