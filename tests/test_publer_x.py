import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import publer_x


class PublerXTests(unittest.TestCase):
    def test_submits_once_without_exposing_key(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'PUBLER_API_KEY': 'test-secret', 'PUBLER_WORKSPACE_ID': 'workspace',
            'PUBLER_X_ACCOUNT_ID': 'x-account',
            'PUBLER_X_STATE_DB': str(Path(directory) / 'state.sqlite3'),
        }):
            message = SimpleNamespace(chat=SimpleNamespace(id=-100), message_id=7,
                                      photo=None, video=None, animation=None)
            submitted = []

            def fake_request(method, path, **kwargs):
                submitted.append((method, path, kwargs))
                return {'job_id': 'job-1'}

            with patch.object(publer_x, 'request', side_effect=fake_request):
                self.assertEqual(asyncio.run(publer_x.publish([message], 'Verified news')), 'job-1')
                self.assertEqual(asyncio.run(publer_x.publish([message], 'Verified news')), 'already-submitted')
            self.assertEqual(len(submitted), 1)
            post = submitted[0][2]['json']['bulk']['posts'][0]
            self.assertEqual(post['networks']['twitter']['type'], 'status')
            self.assertEqual(post['accounts'][0]['id'], 'x-account')

    def test_rejects_overlong_nonpremium_text_before_submission(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'PUBLER_API_KEY': 'test-secret', 'PUBLER_WORKSPACE_ID': 'workspace',
            'PUBLER_X_ACCOUNT_ID': 'x-account',
            'PUBLER_X_STATE_DB': str(Path(directory) / 'state.sqlite3'),
            'PUBLER_X_LONG_POST': 'false',
        }):
            message = SimpleNamespace(chat=SimpleNamespace(id=-100), message_id=8,
                                      photo=None, video=None, animation=None)
            with patch.object(publer_x, 'request') as request:
                with self.assertRaises(ValueError):
                    asyncio.run(publer_x.publish([message], 'a' * 281))
                request.assert_not_called()
