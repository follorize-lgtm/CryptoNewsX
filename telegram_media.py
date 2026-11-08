"""Find the postable picture or video in a Telegram channel message."""

# Telegram's Bot API refuses getFile for anything larger than this.
BOT_DOWNLOAD_LIMIT = 20 * 1024 * 1024

# Images or videos sent "as a file" arrive as documents, not photo/video.
DOCUMENT_TYPES = {
    'image/jpeg': ('jpg', 'image/jpeg', 'photo'),
    'image/png': ('png', 'image/png', 'photo'),
    'video/mp4': ('mp4', 'video/mp4', 'video'),
}


def media_source(message):
    """Return (telegram_file, extension, mime, kind) or None.

    kind is 'photo' or 'video'.
    """
    if getattr(message, 'photo', None):
        return message.photo[-1], 'jpg', 'image/jpeg', 'photo'
    source = getattr(message, 'video', None) or getattr(message, 'animation', None)
    if source:
        return source, 'mp4', 'video/mp4', 'video'
    document = getattr(message, 'document', None)
    if document:
        found = DOCUMENT_TYPES.get((document.mime_type or '').lower())
        if found:
            return (document,) + found
    return None


def check_downloadable(source):
    size = getattr(source, 'file_size', None) or 0
    if size > BOT_DOWNLOAD_LIMIT:
        raise ValueError('Telegram media is %.1f MB; bots can only download up to 20 MB'
                         % (size / 1024 / 1024))
