import asyncio
from pathlib import Path
from types import SimpleNamespace

import schedchie_x


def test_x_caption_and_post_id():
    assert len(schedchie_x._caption('a' * 300)) <= 280
    assert schedchie_x._caption('Short news') == 'Short news'
    assert schedchie_x._post_id({'post': {'id': 'post-123'}}) == 'post-123'
    assert schedchie_x._post_id({'accountId': 'account-123'}) == ''


def test_schedules_telegram_text_once(monkeypatch, tmp_path):
    monkeypatch.setenv('SCHEDCHIE_X_STATE_DB', str(tmp_path / 'state.sqlite3'))
    monkeypatch.setenv('SCHEDCHIE_X_ACCOUNT_ID', 'x-account')
    calls = []

    class Client:
        def call(self, name, args):
            calls.append((name, args))
            return {'content': [{'type': 'text', 'text': '{"postId":"post-123"}'}]}

    monkeypatch.setattr(schedchie_x, '_client', Client)
    message = SimpleNamespace(chat=SimpleNamespace(id=-1001), message_id=42,
                              photo=None, video=None, animation=None)
    assert asyncio.run(schedchie_x.publish([message], 'Breaking: market update')) == 'post-123'
    assert asyncio.run(schedchie_x.publish([message], 'Breaking: market update')) == 'post-123'
    assert len(calls) == 1
    assert calls[0][0] == 'schedule_post'
    assert calls[0][1]['platformSpecificContent'] == {
        'x-account': {'x_text': 'Breaking: market update'}}


def test_uncertain_schedule_is_not_resubmitted(monkeypatch, tmp_path):
    monkeypatch.setenv('SCHEDCHIE_X_STATE_DB', str(tmp_path / 'state.sqlite3'))
    monkeypatch.setenv('SCHEDCHIE_X_ACCOUNT_ID', 'x-account')
    calls = []

    class Client:
        def call(self, name, args):
            calls.append(name)
            raise schedchie_x.SchedchieError('Connection lost after submission')

    monkeypatch.setattr(schedchie_x, '_client', Client)
    message = SimpleNamespace(chat=SimpleNamespace(id=-1001), message_id=43,
                              photo=None, video=None, animation=None)
    try:
        asyncio.run(schedchie_x.publish([message], 'Market update'))
    except schedchie_x.SchedchieError:
        pass
    else:
        raise AssertionError('Expected uncertain submission')
    assert asyncio.run(schedchie_x.publish([message], 'Market update')) == 'already-submitting'
    assert calls == ['schedule_post']


class _File:
    def __init__(self, data=b'jpeg-bytes', size=None):
        self.data = data
        self.file_size = size or len(data)

    async def get_file(self):
        return self

    async def download_to_drive(self, path):
        Path(path).write_bytes(self.data)


def _photo_message(message_id, data=b'jpeg-bytes'):
    return SimpleNamespace(chat=SimpleNamespace(id=-1001), message_id=message_id,
                           photo=[_File(data)], video=None, animation=None, document=None)


def _schedchie_env(monkeypatch, tmp_path):
    monkeypatch.setenv('SCHEDCHIE_X_STATE_DB', str(tmp_path / 'state.sqlite3'))
    monkeypatch.setenv('SCHEDCHIE_X_ACCOUNT_ID', 'x-account')
    monkeypatch.setenv('SCHEDCHIE_MEDIA_UPLOAD_URL', 'https://relay.example')
    monkeypatch.setenv('SCHEDCHIE_MEDIA_BASE_URL', 'https://cdn.example')
    monkeypatch.setenv('SCHEDCHIE_MEDIA_UPLOAD_TOKEN', 'token')
    monkeypatch.setattr(schedchie_x.asyncio, 'sleep', _no_sleep)


async def _no_sleep(_seconds):
    return None


def _response(status):
    return SimpleNamespace(status_code=status, text='')


def test_photo_is_attached_and_transient_media_failure_is_retried(monkeypatch, tmp_path):
    _schedchie_env(monkeypatch, tmp_path)
    puts = []
    monkeypatch.setattr(schedchie_x.requests, 'head', lambda *a, **k: _response(404))

    def put(url, **kwargs):
        puts.append(url)
        return _response(502 if len(puts) == 1 else 200)

    monkeypatch.setattr(schedchie_x.requests, 'put', put)
    calls = []

    class Client:
        def call(self, name, args):
            calls.append((name, args))
            if name == 'upload_media':
                return {'content': [{'type': 'text', 'text': '{"media":{"gcsObjectName":"obj-1"}}'}]}
            return {'content': [{'type': 'text', 'text': '{"postId":"post-9"}'}]}

    monkeypatch.setattr(schedchie_x, '_client', Client)
    assert asyncio.run(schedchie_x.publish([_photo_message(50)], 'Pic news')) == 'post-9'
    assert len(puts) == 2
    schedule = [args for name, args in calls if name == 'schedule_post']
    assert schedule[0]['media'] == [{'gcsObjectName': 'obj-1'}]


def test_already_staged_picture_is_reused(monkeypatch, tmp_path):
    _schedchie_env(monkeypatch, tmp_path)
    monkeypatch.setattr(schedchie_x.requests, 'head', lambda *a, **k: _response(200))

    def put(*a, **k):
        raise AssertionError('should not re-upload an existing content-addressed file')

    monkeypatch.setattr(schedchie_x.requests, 'put', put)

    class Client:
        def call(self, name, args):
            if name == 'upload_media':
                return {'content': [{'type': 'text', 'text': '{"data":{"gcsObjectName":"obj-2"}}'}]}
            assert args['media'] == [{'gcsObjectName': 'obj-2'}]
            return {'content': [{'type': 'text', 'text': '{"postId":"post-10"}'}]}

    monkeypatch.setattr(schedchie_x, '_client', Client)
    assert asyncio.run(schedchie_x.publish([_photo_message(51)], 'Again')) == 'post-10'


def test_image_sent_as_file_is_attached(monkeypatch, tmp_path):
    _schedchie_env(monkeypatch, tmp_path)
    monkeypatch.setattr(schedchie_x.requests, 'head', lambda *a, **k: _response(404))
    monkeypatch.setattr(schedchie_x.requests, 'put', lambda *a, **k: _response(200))
    mimes = []

    class Client:
        def call(self, name, args):
            if name == 'upload_media':
                mimes.append(args['mimeType'])
                return {'content': [{'type': 'text', 'text': '{"gcsObjectName":"obj-3"}'}]}
            assert args['media'] == [{'gcsObjectName': 'obj-3'}]
            return {'content': [{'type': 'text', 'text': '{"postId":"post-11"}'}]}

    document = _File(b'png-bytes')
    document.mime_type = 'image/png'
    message = SimpleNamespace(chat=SimpleNamespace(id=-1001), message_id=52, photo=(),
                              video=None, animation=None, document=document)
    monkeypatch.setattr(schedchie_x, '_client', Client)
    assert asyncio.run(schedchie_x.publish([message], 'File pic')) == 'post-11'
    assert mimes == ['image/png']
