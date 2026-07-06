import re
import time


TOO_LONG_MARKERS = (
    "too long",
    "over 280",
    "maximum allowed length",
    "character limit",
    "tweet needs to be a bit shorter",
    "post needs to be a bit shorter",
)


class PartialThreadPublishError(Exception):
    pass


def _tweet_id(response):
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return data.get("id")
    if hasattr(data, "id"):
        return data.id
    if isinstance(response, dict):
        payload = response.get("data")
        if isinstance(payload, dict):
            return payload.get("id")
    return None


def is_too_long_error(exc):
    details = [str(exc)]
    for attr in ("api_messages", "api_codes", "errors"):
        value = getattr(exc, attr, None)
        if value:
            details.append(str(value))
    response = getattr(exc, "response", None)
    text = getattr(response, "text", None)
    if text:
        details.append(str(text))
    message = " ".join(details).lower()
    return any(marker in message for marker in TOO_LONG_MARKERS)


def split_thread(text, max_chars=280):
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    parts = []
    current = ""
    for token in re.findall(r"\S+\s*", text):
        token = token if current else token.lstrip()
        while len(token) > max_chars:
            if current:
                parts.append(current.rstrip())
                current = ""
            parts.append(token[:max_chars].rstrip())
            token = token[max_chars:].lstrip()
        if len(current) + len(token) <= max_chars:
            current += token
        else:
            if current:
                parts.append(current.rstrip())
            current = token.lstrip()

    if current.strip():
        parts.append(current.rstrip())
    return parts


def _create_post(client, text, reply_to_id=None):
    kwargs = {"text": text}
    if reply_to_id:
        kwargs["in_reply_to_tweet_id"] = reply_to_id
    return client.create_tweet(**kwargs)


def _send_thread(client, parts, thread_delay):
    reply_to_id = None
    posted = 0
    for index, part in enumerate(parts):
        try:
            response = _create_post(client, part, reply_to_id)
        except Exception as exc:
            if posted:
                raise PartialThreadPublishError(
                    f"thread stopped after {posted} of {len(parts)} posts: {exc}"
                ) from exc
            raise
        posted += 1
        tweet_id = _tweet_id(response)
        if index < len(parts) - 1 and not tweet_id:
            raise PartialThreadPublishError(
                f"thread stopped after {posted} of {len(parts)} posts: missing X post id"
            )
        reply_to_id = tweet_id
        if index < len(parts) - 1 and thread_delay > 0:
            time.sleep(thread_delay)
    return posted


def send_text(client, text, max_chars=280, thread_delay=2.0):
    text = text.strip()
    if not text:
        return 0

    too_long_exc = None
    try:
        _create_post(client, text)
        return 1
    except Exception as exc:
        if len(text) <= max_chars or not is_too_long_error(exc):
            raise
        too_long_exc = exc

    parts = split_thread(text, max_chars)
    if len(parts) <= 1:
        raise too_long_exc
    return _send_thread(client, parts, thread_delay)
