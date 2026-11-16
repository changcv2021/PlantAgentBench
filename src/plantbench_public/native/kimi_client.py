"""Durable OpenAI-compatible client for the official Kimi API.

Policy (mirrors the lab harness): the first HTTP 200 for a logical request is
final. A resumed run replays it from disk; it is never re-sampled because its
content later turned out invalid. Only transport failures and 408/429/5xx are
retried, with every attempt (including error bodies) kept for audit. Request
bodies are stored with image bytes deduplicated by SHA-256. The API key is
never written to disk by this module.
"""
import base64
import copy
import hashlib
import json
import os
import random
import sqlite3
import stat
import time
import zlib

import requests

RETRIABLE = {408, 409, 429, 500, 502, 503, 504, 529}


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


class ApiFailure(RuntimeError):
    """Infrastructure failure: no HTTP 200 envelope for this logical request."""


def load_api_key(env_var, key_file):
    if not env_var and not key_file:
        return None  # local vLLM endpoint: no credential
    key = os.environ.get(env_var) if env_var else None
    if not key and key_file:
        mode = os.stat(key_file).st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise PermissionError('API key file must be private (chmod 600): ' + key_file)
        with open(key_file) as handle:
            key = handle.read().strip()
    if not key:
        raise RuntimeError('Kimi API key not found: set $' + str(env_var) + ' or api_key_file')
    return key


class KimiStore:
    def __init__(self, path, identity):
        self.db = sqlite3.connect(path, timeout=60)
        self.db.executescript(
            'CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);'
            'CREATE TABLE IF NOT EXISTS images (sha TEXT PRIMARY KEY, mime TEXT, bytes BLOB);'
            'CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, body_hash TEXT, body BLOB);'
            'CREATE TABLE IF NOT EXISTS attempts (request_id TEXT, number INTEGER, record BLOB, PRIMARY KEY(request_id, number));')
        serialized = json.dumps(identity, sort_keys=True)
        old = self.db.execute('SELECT value FROM metadata WHERE key=?', ('identity',)).fetchone()
        if old and old[0] != serialized:
            raise ValueError('Request store identity changed; use a new run directory for a new configuration')
        self.db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', ('identity', serialized))
        self.db.commit()

    def _pack(self, body):
        packed = copy.deepcopy(body)
        for message in packed['messages']:
            if not isinstance(message.get('content'), list):
                continue
            for block in message['content']:
                if block.get('type') != 'image_url':
                    continue
                header, payload = block['image_url']['url'].split(',', 1)
                raw = base64.b64decode(payload, validate=True)
                sha = hashlib.sha256(raw).hexdigest()
                self.db.execute('INSERT OR IGNORE INTO images VALUES (?,?,?)', (sha, header[5:].split(';')[0], raw))
                block['image_url']['url'] = {'packed_image_sha256': sha, 'original_prefix': header + ','}
        return zlib.compress(encoded(packed), 6)

    def restore_body(self, request_id):
        row = self.db.execute('SELECT body_hash, body FROM requests WHERE id=?', (request_id,)).fetchone()
        body = json.loads(zlib.decompress(row[1]))
        for message in body['messages']:
            for block in message['content'] if isinstance(message.get('content'), list) else []:
                if block.get('type') == 'image_url':
                    ref = block['image_url']['url']
                    raw = self.db.execute('SELECT bytes FROM images WHERE sha=?', (ref['packed_image_sha256'],)).fetchone()[0]
                    block['image_url']['url'] = ref['original_prefix'] + base64.b64encode(raw).decode('ascii')
        if digest(body) != row[0]:
            raise ValueError('Stored request reconstruction mismatch')
        return body

    def register(self, request_id, body):
        body_hash = digest(body)
        row = self.db.execute('SELECT body_hash FROM requests WHERE id=?', (request_id,)).fetchone()
        if row and row[0] != body_hash:
            raise ValueError('Logical request body changed on resume: ' + request_id)
        if not row:
            self.db.execute('INSERT INTO requests VALUES (?,?,?)', (request_id, body_hash, self._pack(body)))
            self.db.commit()
        return body_hash

    def attempts(self, request_id):
        return [(n, json.loads(zlib.decompress(b))) for n, b in
                self.db.execute('SELECT number, record FROM attempts WHERE request_id=? ORDER BY number', (request_id,))]

    def add_attempt(self, request_id, number, record):
        self.db.execute('INSERT INTO attempts VALUES (?,?,?)', (request_id, number, zlib.compress(encoded(record), 6)))
        self.db.commit()

    def close(self):
        self.db.close()


class KimiClient:
    def __init__(self, base_url, api_key, *, timeout=1800, max_attempts=8, max_backoff=120):
        self.url = base_url.rstrip('/') + '/chat/completions'
        self.api_key = api_key
        self.timeout, self.max_attempts, self.max_backoff = timeout, max_attempts, max_backoff

    def _post(self, body):
        started = time.monotonic()
        try:
            resp = requests.post(self.url, data=encoded(body), timeout=(30, self.timeout),
                                 headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + self.api_key} if self.api_key else {})})
        except requests.RequestException as exc:
            # Never persist exception text: it may include URLs or headers.
            return {'http_status': None, 'transport_error': type(exc).__name__, 'elapsed_seconds': time.monotonic() - started}
        keep = {k.lower(): v for k, v in resp.headers.items()
                if k.lower() in {'x-request-id', 'msh-request-id', 'retry-after', 'date', 'content-type'}}
        return {'http_status': resp.status_code, 'response_base64': base64.b64encode(resp.content).decode('ascii'),
                'headers': keep, 'transport_error': None, 'elapsed_seconds': time.monotonic() - started,
                'received_at_unix': time.time()}

    def complete(self, store, request_id, body):
        """Return the decoded JSON envelope of the first HTTP 200 for request_id."""
        body_hash = store.register(request_id, body)
        saved = store.attempts(request_id)
        for number, record in saved:
            if record.get('http_status') == 200:
                return self._decoded(record, number, body_hash, replayed=True)
        start = (saved[-1][0] + 1) if saved else 1
        last = None
        for number in range(start, start + self.max_attempts):
            record = self._post(body)
            record.update(request_hash=body_hash, attempt=number)
            store.add_attempt(request_id, number, record)
            status = record.get('http_status')
            if status == 200:
                return self._decoded(record, number, body_hash, replayed=False)
            last = record
            if status is not None and status not in RETRIABLE:
                raise ApiFailure('NONRETRIABLE_HTTP_%s: %s' % (status, self._error_text(record)))
            retry_after = (record.get('headers') or {}).get('retry-after')
            delay = float(retry_after) if retry_after and retry_after.replace('.', '', 1).isdigit() else \
                min(self.max_backoff, 2 ** (number - start + 1)) * (0.5 + random.random())
            time.sleep(delay)
        raise ApiFailure('RETRIES_EXHAUSTED: %s' % (self._error_text(last) if last else 'no attempt'))

    @staticmethod
    def _error_text(record):
        if record.get('response_base64'):
            return base64.b64decode(record['response_base64']).decode('utf-8', 'replace')[:500]
        return str(record.get('transport_error'))

    @staticmethod
    def _decoded(record, number, body_hash, replayed):
        raw = base64.b64decode(record['response_base64'])
        envelope = json.loads(raw)
        return {'envelope': envelope, 'response_sha256': hashlib.sha256(raw).hexdigest(), 'attempt': number,
                'request_hash': body_hash, 'elapsed_seconds': record['elapsed_seconds'],
                'server_request_id': (record.get('headers') or {}).get('x-request-id') or (record.get('headers') or {}).get('msh-request-id'),
                'replayed_from_store': replayed}
