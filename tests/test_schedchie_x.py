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
