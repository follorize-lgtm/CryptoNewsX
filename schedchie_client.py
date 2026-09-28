"""Private Schedchie OAuth renewal and MCP client for the X cross-poster."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import time

import requests

MCP_URL = 'https://mcp.schedchie.com/mcp'
TOKEN_URL = 'https://mcp.schedchie.com/token'
_lock = threading.Lock()


class SchedchieError(RuntimeError):
    pass


@contextmanager
def credential_lock(path):
    with _lock, path.with_suffix('.lock').open('a') as handle:
        os.chmod(handle.name, 0o600)
        if os.name == 'posix':
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'posix':
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def save_credentials(path, data):
    """Persist rotating refresh tokens before another process can use them."""
    fd, name = tempfile.mkstemp(prefix='.schedchie-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _post(session, url, **kwargs):
    try:
        response = session.post(url, timeout=40, allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise SchedchieError('Schedchie connection failed; credentials were not logged') from None
    if not 200 <= response.status_code < 300:
        raise SchedchieError(f'Schedchie HTTP {response.status_code}; reconnect if authorization expired')
    return response


def access_credentials(path, session, *, force_refresh=False):
    path = Path(path)
    if not path.is_file():
        raise SchedchieError('Schedchie credentials have not been configured')
    with credential_lock(path):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            expiry = float(data['obtained_at']) + float(data['expires_in'])
            if not data.get('access_token') or not data.get('client_id'):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise SchedchieError('Invalid Schedchie credential file') from None
        if not force_refresh and expiry > time.time() + 300:
            return data
        if not data.get('refresh_token'):
            raise SchedchieError('Schedchie needs reconnection; no refresh credential is available')
        response = _post(session, TOKEN_URL, data={
            'grant_type': 'refresh_token', 'client_id': data['client_id'],
            'refresh_token': data['refresh_token'],
        })
        try:
            refreshed = response.json()
            if not refreshed.get('access_token') or float(refreshed.get('expires_in', 0)) <= 0:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise SchedchieError('Schedchie returned an invalid renewal response') from None
        data.update(refreshed, obtained_at=time.time())
        save_credentials(path, data)
        return data


def rpc_result(response, request_id):
    try:
        if 'text/event-stream' in response.headers.get('Content-Type', ''):
            messages = []
            parts = []
            for line in response.text.splitlines():
                if line.startswith('data:'):
                    parts.append(line[5:].lstrip())
                elif not line.strip():
                    if parts:
                        messages.append(_decode_sse_json('\n'.join(parts)))
                        parts = []
                elif parts and not line.startswith(('event:', 'id:', 'retry:', ':')):
                    # Schedchie has emitted literal newlines inside a JSON
                    # tool-result string without the required SSE data prefix.
                    parts.append(line)
            if parts:
                messages.append(_decode_sse_json('\n'.join(parts)))
            data = next(item for item in messages if item.get('id') == request_id)
        else:
            data = response.json()
        if data.get('id') != request_id or data.get('error') or 'result' not in data:
            raise ValueError()
        return data['result']
    except (ValueError, TypeError, AttributeError, StopIteration):
        raise SchedchieError('Schedchie returned an invalid MCP response') from None


def _decode_sse_json(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Recover only malformed control characters inside quoted strings.
        # Keep normal JSON syntax validation and the response-id/error checks.
        repaired = []
        quoted = escaped = False
        for char in raw:
            if escaped:
                repaired.append(char)
                escaped = False
            elif char == '\\':
                repaired.append(char)
                escaped = True
            elif char == '"':
                repaired.append(char)
                quoted = not quoted
            elif quoted and char in '\n\r\t':
                repaired.append({'\n': '\\n', '\r': '\\r', '\t': '\\t'}[char])
            else:
                repaired.append(char)
        return json.loads(''.join(repaired))


def check_connection(path, *, force_refresh=False):
    """List connected accounts without publishing, uploading, or changing settings."""
    with requests.Session() as session:
        creds = access_credentials(path, session, force_refresh=force_refresh)
        session.headers.update({'Authorization': 'Bearer ' + creds['access_token'],
                                'Accept': 'application/json, text/event-stream'})
        client = Client(path, session=session, credentials=creds)
        result = client.call('list_accounts')
        if result.get('isError'):
            raise SchedchieError('Schedchie account lookup failed')
        try:
            groups = json.loads(next(item['text'] for item in result['content'] if item.get('type') == 'text'))
            if not isinstance(groups, list):
                raise ValueError()
        except (ValueError, KeyError, TypeError, StopIteration):
            raise SchedchieError('Schedchie returned invalid account groups') from None
        # Deliberately omit raw provider fields, which may contain credential health details.
        return {'connected': True, 'account_groups': len(groups),
                'refresh_available': bool(creds.get('refresh_token')),
                'expires_at': int(float(creds['obtained_at']) + float(creds['expires_in'])),
                'scopes': creds.get('scope', ''), 'posting_enabled_by_this_check': False}


class Client:
    """Minimal authenticated MCP client; credentials remain in the private file."""

    def __init__(self, path, *, session=None, credentials=None):
        self.path = Path(path)
        self.session = session or requests.Session()
        self.credentials = credentials or access_credentials(self.path, self.session)
        self.session.headers.update({'Authorization': 'Bearer ' + self.credentials['access_token'],
                                     'Accept': 'application/json, text/event-stream'})
        self._next_id = 1
        self._initialize()

    def _request(self, method, params=None):
        request_id = self._next_id
        self._next_id += 1
        response = _post(self.session, MCP_URL, json={'jsonrpc': '2.0', 'id': request_id,
                         'method': method, 'params': params or {}})
        return rpc_result(response, request_id), response

    def _initialize(self):
        initialized, response = self._request('initialize', {'protocolVersion': '2025-03-26',
            'capabilities': {}, 'clientInfo': {'name': 'wabble-vps', 'version': '1.0'}})
        self.session.headers['MCP-Protocol-Version'] = initialized.get('protocolVersion', '2025-03-26')
        if response.headers.get('Mcp-Session-Id'):
            self.session.headers['Mcp-Session-Id'] = response.headers['Mcp-Session-Id']
        _post(self.session, MCP_URL, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def call(self, name, arguments=None):
        result, _ = self._request('tools/call', {'name': name, 'arguments': arguments or {}})
        if result.get('isError'):
            raise SchedchieError(f'Schedchie {name} failed')
        return result


def tool_json(result):
    """Extract the JSON object returned by a Schedchie MCP tool safely."""
    try:
        text = next(item['text'] for item in result['content'] if item.get('type') == 'text')
        # The provider has also emitted literal newlines inside the nested
        # tool-result JSON string, even when the outer MCP event was valid.
        value = _decode_sse_json(text)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (KeyError, TypeError, ValueError, StopIteration):
        raise SchedchieError('Schedchie returned an invalid tool result') from None
