"""Integration test: starts the real HTTP server and screens a document through the API."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import date
from pathlib import Path

import cv2
import pytest

ROOT = Path(__file__).parent.parent


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _multipart(fields: dict, files: dict):
    boundary = uuid.uuid4().hex
    body = b''
    for k, v in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for k, (name, data, ctype) in files.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                 f'Content-Type: {ctype}\r\n\r\n').encode() + data + b'\r\n'
    body += f'--{boundary}--\r\n'.encode()
    return body, f'multipart/form-data; boundary={boundary}'


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    port = _free_port()
    env = dict(os.environ, SCREENING_DATA_DIR=str(tmp_path_factory.mktemp('data')))
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--port', str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'
    for _ in range(120):
        try:
            urllib.request.urlopen(base + '/health', timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail('server did not start')
    yield base
    proc.kill()


def test_health_and_console(server):
    health = json.loads(urllib.request.urlopen(server + '/health').read())
    assert health['status'] == 'ok' and health['audit']['valid']
    assert b'Document Screening Console' in urllib.request.urlopen(server + '/').read()


def test_screen_stolen_passport_is_referred(server):
    from synthdocs import render_document
    doc = render_document('passport', seed=2024, today=date.today())
    req = urllib.request.Request(server + '/api/v1/watchlist', method='POST', headers={'Content-Type': 'application/json'},
                                 data=json.dumps(dict(list_type='LOST_STOLEN', reason='test report',
                                                      document_number=doc.fields['document_number'])).encode())
    assert json.loads(urllib.request.urlopen(req).read())['id']
    body, ctype = _multipart(dict(checkpoint_id='TEST-ICP'), dict(document=('p.jpg', cv2.imencode('.jpg', doc.image)[1].tobytes(), 'image/jpeg')))
    req = urllib.request.Request(server + '/api/v1/screen', data=body, method='POST', headers={'Content-Type': ctype})
    report = json.loads(urllib.request.urlopen(req, timeout=300).read())
    assert report['documents'][0]['document_type'] == 'passport'
    assert report['risk']['disposition'] == 'REFER_TO_SUPERVISOR'
    assert any(r['code'].startswith('watchlist_') for r in report['risk']['reasons'])
    verify = json.loads(urllib.request.urlopen(server + '/api/v1/audit/verify').read())
    assert verify['valid'] and verify['records'] >= 2


def test_rejects_non_images(server):
    body, ctype = _multipart({}, dict(document=('x.txt', b'hello', 'text/plain')))
    req = urllib.request.Request(server + '/api/v1/screen', data=body, method='POST', headers={'Content-Type': ctype})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 415
