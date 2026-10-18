import hashlib
import json
import os
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from companion import local_desktop, server


@pytest.fixture
def browser_client(tmp_path, monkeypatch):
    if os.name != 'nt':
        pytest.skip('Local Windows browser connection')
    monkeypatch.delenv('RC_PUBLIC_URL', raising=False)
    monkeypatch.delenv('RC_SETUP_TOKEN', raising=False)
    app = server.create_app(tmp_path, background=False)
    with TestClient(app, base_url='http://127.0.0.1:8787', client=('127.0.0.1', 45000)) as client:
        assert client.post('/api/auth/setup', json={'password': 'test'}).status_code == 200
        yield client


def test_prepare_open_and_capability_scope(browser_client, monkeypatch):
    c = browser_client
    launch = Mock()
    monkeypatch.setattr(server, 'open_regular_browser', launch)
    response = c.post('/api/browser/connect')
    assert response.status_code == 200
    store = c.app.state.store
    extension = store.directory / 'browser-extension'
    key = json.loads((extension / 'config.json').read_text())['key']
    assert key not in response.text and key not in c.get('/api/state').text
    assert store.secret('browser_bridge_hash') == hashlib.sha256(key.encode()).hexdigest()
    assert c.post('/api/browser/connect').status_code == 200
    assert json.loads((extension / 'config.json').read_text())['key'] == key
    launch.assert_called_with(store.directory)
    c.post('/api/auth/logout')
    headers = {'Authorization': 'Bearer ' + key, 'Origin': 'chrome-extension://example'}
    assert c.post('/api/agent/heartbeat', headers=headers, json={'id': 'test'}).status_code != 200
    assert c.post('/api/browser/connect', headers=headers).status_code != 200
    assert c.post('/api/browser/extensions', headers=headers).status_code != 200
    body = {'state': 'signed_in', 'session': {'token': 'private-session', 'uudid': 'device'}}
    assert c.post('/api/browser/session', headers=headers, json=body).status_code == 200
    assert store.secret('railway')['token'] == 'private-session'
    assert store.read()['browser_connection']['status'] == 'signed_in'
    assert store.read()['agent'].get('ready') is False
    assert store.read()['mode'] == 'away'
    assert c.post('/api/browser/session', headers=headers, json={'state': 'signed_out'}).status_code == 200
    assert store.secret('railway') is None


def test_bridge_rejects_invalid_credentials_and_cross_site_posts(browser_client):
    c = browser_client
    store = c.app.state.store
    key = 'browser-capability'
    store.set_secret('browser_bridge_hash', hashlib.sha256(key.encode()).hexdigest())
    body = {'state': 'signed_in', 'session': {'token': 'private-session', 'uudid': 'device'}}
    assert c.post('/api/browser/session', json=body).status_code == 401
    headers = {'Authorization': 'Bearer wrong', 'Origin': 'chrome-extension://example'}
    assert c.post('/api/browser/session', headers=headers, json=body).status_code == 401
    headers = {'Authorization': 'Bearer ' + key, 'Origin': 'https://foreign.example'}
    assert c.post('/api/browser/session', headers=headers, json=body).status_code == 403
    headers['Origin'] = 'chrome-extension://example'
    body['session']['token'] = 'bad\r\nheader'
    assert c.post('/api/browser/session', headers=headers, json=body).status_code == 400
    assert not store.secret('railway')
    # Signing out of the extension must not erase an independently saved session.
    c.put('/api/connections/railway', json={'token': 'manual-session', 'uudid': 'manual-device'})
    assert c.post('/api/browser/session', headers=headers, json={'state': 'signed_out'}).status_code == 200
    assert store.secret('railway')['token'] == 'manual-session'


def test_normal_browser_uses_default_profile_without_automation(tmp_path, monkeypatch):
    executable = tmp_path / 'brave.exe'
    executable.touch()
    (tmp_path / 'desktop').mkdir()
    (tmp_path / 'desktop' / 'browser.json').write_text(json.dumps({'binary': str(executable), 'name': 'Brave'}))
    launch = Mock()
    monkeypatch.setattr(local_desktop.subprocess, 'Popen', launch)
    local_desktop.open_regular_browser(tmp_path)
    assert launch.call_args.args[0] == [str(executable), 'https://eticket.railway.gov.bd/login']
    local_desktop.open_regular_browser(tmp_path, 'extensions')
    assert launch.call_args.args[0] == [str(executable), 'brave://extensions/']
